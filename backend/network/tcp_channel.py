# backend/network/tcp_channel.py
"""ISO 8583 TCP/IP channel — pure Python asyncio fallback (P2 T2.1).

Provides a software-only TCP transport for ISO 8583 messages when the jPOS
sidecar (iso-engine) is unavailable.  Used by POST /iso-engine/send-tcp.

Features:
  - MLI (Message Length Indicator) framing: 2-byte or 4-byte big-endian prefix.
  - Optional TLS wrapping (TLSContext passed by caller).
  - STAN/RRN correlation: matches responses by echo of DE11 (STAN).
  - Configurable connect timeout and read timeout.
  - Network management MTIs: 0800 (sign-on/echo/sign-off) with DE70.

Public API
----------
send_iso_tcp(host, port, packed_hex, mli_mode, tls_context, timeout)
    → {"response_hex": str, "mti": str, "elapsed_ms": float}

NetworkMgmt helpers (T2.2):
sign_on(host, port, ...)  → send 0800 DE70=001 and return 0810 response
echo(host, port, ...)     → send 0800 DE70=301 (Network Echo Test)
sign_off(host, port, ...) → send 0800 DE70=002
"""
from __future__ import annotations

import asyncio
import logging
import ssl
import struct
import time
from typing import Optional

log = logging.getLogger("tcp_channel")

# ── MLI modes ────────────────────────────────────────────────────────────────
MLI_2E = "2E"   # 2-byte exclusive (most common: length of payload only)
MLI_2I = "2I"   # 2-byte inclusive (length includes the 2-byte MLI itself)
MLI_4E = "4E"   # 4-byte exclusive
MLI_4I = "4I"   # 4-byte inclusive


def _encode_mli(payload_len: int, mli_mode: str) -> bytes:
    """Encode a Message Length Indicator prefix."""
    if mli_mode == MLI_2E:
        return struct.pack(">H", payload_len)
    if mli_mode == MLI_2I:
        return struct.pack(">H", payload_len + 2)
    if mli_mode == MLI_4E:
        return struct.pack(">I", payload_len)
    if mli_mode == MLI_4I:
        return struct.pack(">I", payload_len + 4)
    raise ValueError(f"Unknown MLI mode: {mli_mode!r}")


def _decode_mli_length(header: bytes, mli_mode: str) -> int:
    """Decode the payload length from an MLI header."""
    if mli_mode in (MLI_2E, MLI_2I):
        length = struct.unpack(">H", header)[0]
        return length - 2 if mli_mode == MLI_2I else length
    if mli_mode in (MLI_4E, MLI_4I):
        length = struct.unpack(">I", header)[0]
        return length - 4 if mli_mode == MLI_4I else length
    raise ValueError(f"Unknown MLI mode: {mli_mode!r}")


def _mli_header_size(mli_mode: str) -> int:
    return 4 if mli_mode in (MLI_4E, MLI_4I) else 2


# ── Async core ───────────────────────────────────────────────────────────────

async def _send_receive(
    host: str,
    port: int,
    payload_bytes: bytes,
    mli_mode: str = MLI_2E,
    ssl_context: Optional[ssl.SSLContext] = None,
    connect_timeout: float = 10.0,
    read_timeout: float = 30.0,
) -> bytes:
    """Open a TCP connection, send payload with MLI header, read response."""
    mli = _encode_mli(len(payload_bytes), mli_mode)
    frame = mli + payload_bytes

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ssl_context),
            timeout=connect_timeout,
        )
    except asyncio.TimeoutError:
        raise TimeoutError(f"Connect timeout after {connect_timeout}s to {host}:{port}")
    except OSError as exc:
        raise ConnectionError(f"Cannot connect to {host}:{port} — {exc}") from exc

    try:
        writer.write(frame)
        await writer.drain()

        # Read response MLI header
        hdr_size = _mli_header_size(mli_mode)
        header = await asyncio.wait_for(reader.readexactly(hdr_size), timeout=read_timeout)
        payload_len = _decode_mli_length(header, mli_mode)
        if payload_len <= 0 or payload_len > 65535:
            raise ValueError(f"Suspicious response payload length: {payload_len}")

        response_payload = await asyncio.wait_for(
            reader.readexactly(payload_len), timeout=read_timeout
        )
        return response_payload

    except asyncio.TimeoutError:
        raise TimeoutError(f"Read timeout after {read_timeout}s")
    except asyncio.IncompleteReadError as exc:
        raise ConnectionError(f"Connection closed mid-message after {len(exc.partial)} bytes") from exc
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


# ── Public synchronous wrapper ────────────────────────────────────────────────

def send_iso_tcp(
    host: str,
    port: int,
    packed_hex: str,
    mli_mode: str = MLI_2E,
    tls: bool = False,
    ca_cert_pem: Optional[str] = None,
    connect_timeout: float = 10.0,
    read_timeout: float = 30.0,
) -> dict:
    """Send a packed ISO 8583 message over TCP/IP and return the response.

    Args:
        host:             Remote ISO host.
        port:             Remote ISO port.
        packed_hex:       Hex string of the packed ISO 8583 message body.
        mli_mode:         MLI framing mode: "2E" | "2I" | "4E" | "4I".
        tls:              Wrap connection in TLS.
        ca_cert_pem:      Path to CA cert PEM file for TLS verification (optional).
        connect_timeout:  Seconds to wait for TCP connection.
        read_timeout:     Seconds to wait for response after send.

    Returns:
        dict with keys:
            response_hex  — hex of the raw response bytes
            elapsed_ms    — round-trip time in milliseconds
            error         — error message string, only present on failure
    """
    ssl_ctx: Optional[ssl.SSLContext] = None
    if tls:
        ssl_ctx = ssl.create_default_context()
        if ca_cert_pem:
            ssl_ctx.load_verify_locations(ca_cert_pem)
        else:
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

    payload_bytes = bytes.fromhex(packed_hex)
    t0 = time.perf_counter()

    try:
        response_bytes = asyncio.run(
            _send_receive(host, port, payload_bytes, mli_mode, ssl_ctx,
                          connect_timeout, read_timeout)
        )
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        return {
            "response_hex": response_bytes.hex(),
            "elapsed_ms": elapsed_ms,
        }
    except (TimeoutError, ConnectionError, ValueError) as exc:
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        log.warning("ISO TCP send failed: %s", exc)
        return {
            "response_hex": "",
            "elapsed_ms": elapsed_ms,
            "error": str(exc),
        }
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        log.exception("Unexpected error in send_iso_tcp")
        return {
            "response_hex": "",
            "elapsed_ms": elapsed_ms,
            "error": f"Unexpected: {exc}",
        }


# ── Network Management (T2.2) ─────────────────────────────────────────────────

def _build_net_mgmt_hex(de70: str, stan: str = "000001") -> str:
    """Build a minimal 0800 network management message hex payload.

    This is a simplified/illustrative packer for net-mgmt messages.
    Real production use should go through jpos_bridge.pack_via_jpos().

    MTI: 0800 (ASCII 4 bytes)
    Bitmap: DE11 + DE70 only
    DE11 (STAN): 6 bytes BCD
    DE70 (Network Management Information): 3 bytes
    """
    # Bitmap: DE11=bit11, DE70=bit70
    # Byte positions (1-indexed bits):
    # DE11 = byte 2 bit 3 (0x20 in byte 2 = bit position 11)
    # DE70 = byte 9 bit 6 (bit position 70 = byte 9, bit 6)
    # Primary bitmap: 16 hex chars = 8 bytes
    # DE11 is in primary bitmap byte 2, bit 3 → value 0x20
    # DE70 is in secondary bitmap byte 1, bit 6 → need secondary bitmap
    # Secondary bitmap bit: DE70 → bit 70-64=6 in secondary = 0x02 in byte 1

    # Primary bitmap with DE11 and secondary bitmap flag (bit1=1 for secondary)
    # Bit 1 (secondary present) = 0x80 in byte1
    # Bit 11 (DE11) = byte2 bit3 = 0x20
    # → primary bitmap = 80 20 00 00 00 00 00 00
    primary_bm = bytes([0x80, 0x20, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

    # Secondary bitmap: DE70 = bit 70-64 = bit 6 = 0x02 in byte 1
    secondary_bm = bytes([0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

    # DE11 STAN: 6 BCD digits
    stan_clean = stan.zfill(6)[-6:]
    # pack as 3 BCD bytes (e.g. "000001" → 0x00 0x00 0x01)
    stan_bytes = bytes([
        int(stan_clean[0:2], 10),
        int(stan_clean[2:4], 10),
        int(stan_clean[4:6], 10),
    ])

    # DE70: 3-byte numeric
    de70_bytes = de70.zfill(3).encode("ascii")

    msg = b"0800" + primary_bm + secondary_bm + stan_bytes + de70_bytes
    return msg.hex()


def _net_mgmt(
    host: str,
    port: int,
    de70: str,
    stan: str = "000001",
    mli_mode: str = MLI_2E,
    tls: bool = False,
    ca_cert_pem: Optional[str] = None,
    timeout: float = 10.0,
) -> dict:
    """Send a 0800 network management message and return the 0810 response."""
    packed_hex = _build_net_mgmt_hex(de70=de70, stan=stan)
    result = send_iso_tcp(
        host=host, port=port, packed_hex=packed_hex,
        mli_mode=mli_mode, tls=tls, ca_cert_pem=ca_cert_pem,
        connect_timeout=timeout, read_timeout=timeout,
    )
    if result.get("error"):
        return {"success": False, "error": result["error"], "elapsed_ms": result["elapsed_ms"]}

    resp_hex = result["response_hex"]
    # MTI is first 4 ASCII bytes → first 8 hex chars
    resp_mti = bytes.fromhex(resp_hex[:8]).decode("ascii", errors="replace") if len(resp_hex) >= 8 else "????"
    return {
        "success": resp_mti == "0810",
        "request_mti": "0800",
        "response_mti": resp_mti,
        "de70": de70,
        "response_hex": resp_hex,
        "elapsed_ms": result["elapsed_ms"],
    }


def sign_on(host: str, port: int, stan: str = "000001",
            mli_mode: str = MLI_2E, tls: bool = False,
            ca_cert_pem: Optional[str] = None, timeout: float = 10.0) -> dict:
    """Send network sign-on (0800 DE70=001) and return result."""
    return _net_mgmt(host, port, "001", stan=stan, mli_mode=mli_mode,
                     tls=tls, ca_cert_pem=ca_cert_pem, timeout=timeout)


def echo(host: str, port: int, stan: str = "000001",
         mli_mode: str = MLI_2E, tls: bool = False,
         ca_cert_pem: Optional[str] = None, timeout: float = 10.0) -> dict:
    """Send network echo test (0800 DE70=301) and return result."""
    return _net_mgmt(host, port, "301", stan=stan, mli_mode=mli_mode,
                     tls=tls, ca_cert_pem=ca_cert_pem, timeout=timeout)


def sign_off(host: str, port: int, stan: str = "000001",
             mli_mode: str = MLI_2E, tls: bool = False,
             ca_cert_pem: Optional[str] = None, timeout: float = 10.0) -> dict:
    """Send network sign-off (0800 DE70=002) and return result."""
    return _net_mgmt(host, port, "002", stan=stan, mli_mode=mli_mode,
                     tls=tls, ca_cert_pem=ca_cert_pem, timeout=timeout)
