package com.e2ms.iso;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.eclipse.jetty.server.Server;
import org.eclipse.jetty.servlet.ServletContextHandler;
import org.eclipse.jetty.servlet.ServletHolder;
import org.jpos.iso.ISOException;
import org.jpos.iso.ISOMsg;
import org.jpos.iso.packager.GenericPackager;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import jakarta.servlet.http.HttpServlet;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.io.InputStream;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * e2MS ISO Engine — jPOS Q2 Sidecar
 *
 * Exposes two HTTP endpoints:
 *   POST /pack    — pack a field map + MTI into an ISO 8583 hex string
 *   POST /unpack  — unpack an ISO 8583 hex string back into a field map
 *
 * Request / response shape mirrors backend/network/packer.py so the Python
 * backend can swap behind the same contract:
 *
 *   POST /pack
 *     Request:  { "network": "visa", "mti": "0100", "fields": { "2": "4111...", ... } }
 *     Response: { "hex": "...", "network": "visa", "mti": "0100" }
 *
 *   POST /unpack
 *     Request:  { "network": "visa", "hex": "..." }
 *     Response: { "fields": { "2": "4111...", ... }, "mti": "0100", "network": "visa" }
 *
 * Packager XML files are loaded from the classpath:
 *   /packager/visa.xml
 *   /packager/mastercard.xml
 *   /packager/amex.xml
 *   /packager/discover.xml
 */
public class IsoEngineServer {

    private static final Logger LOG = LoggerFactory.getLogger(IsoEngineServer.class);
    private static final int PORT = Integer.parseInt(
            System.getenv().getOrDefault("ISO_ENGINE_PORT", "8200"));

    // Cache packagers by network name
    private static final ConcurrentHashMap<String, GenericPackager> PACKAGERS =
            new ConcurrentHashMap<>();

    private static final ObjectMapper JSON = new ObjectMapper();

    public static void main(String[] args) throws Exception {
        LOG.info("Starting e2MS ISO Engine on port {}", PORT);

        Server server = new Server(PORT);
        ServletContextHandler ctx = new ServletContextHandler();
        ctx.setContextPath("/");
        ctx.addServlet(new ServletHolder(new HealthServlet()), "/health");
        ctx.addServlet(new ServletHolder(new PackServlet()),   "/pack");
        ctx.addServlet(new ServletHolder(new UnpackServlet()), "/unpack");
        server.setHandler(ctx);

        server.start();
        LOG.info("ISO Engine ready — http://localhost:{}/health", PORT);
        server.join();
    }

    // ------------------------------------------------------------------
    // Packager loader
    // ------------------------------------------------------------------

    private static GenericPackager getPackager(String network) throws ISOException {
        return PACKAGERS.computeIfAbsent(network.toLowerCase(), n -> {
            String resource = "/packager/" + n + ".xml";
            InputStream is = IsoEngineServer.class.getResourceAsStream(resource);
            if (is == null) {
                // Fallback: use the generic ISO 8583 ASCII packager bundled with jPOS
                LOG.warn("No packager XML found at {}; using default ASCII packager", resource);
                try {
                    return new GenericPackager(
                        IsoEngineServer.class.getResourceAsStream("/packager/generic.xml"));
                } catch (Exception e2) {
                    throw new RuntimeException("Cannot load any packager", e2);
                }
            }
            try {
                return new GenericPackager(is);
            } catch (ISOException e) {
                throw new RuntimeException("Failed to load packager for " + n, e);
            }
        });
    }

    // ------------------------------------------------------------------
    // /health
    // ------------------------------------------------------------------

    static class HealthServlet extends HttpServlet {
        @Override
        protected void doGet(HttpServletRequest req, HttpServletResponse resp)
                throws IOException {
            resp.setContentType("application/json");
            resp.getWriter().write("{\"status\":\"ok\",\"service\":\"iso-engine\"}");
        }
    }

    // ------------------------------------------------------------------
    // /pack
    // ------------------------------------------------------------------

    static class PackServlet extends HttpServlet {
        @Override
        @SuppressWarnings("unchecked")
        protected void doPost(HttpServletRequest req, HttpServletResponse resp)
                throws IOException {
            resp.setContentType("application/json");
            try {
                Map<String, Object> body = JSON.readValue(req.getInputStream(), Map.class);
                String network = (String) body.getOrDefault("network", "visa");
                String mti     = (String) body.getOrDefault("mti", "0100");
                Map<String, String> fields = (Map<String, String>) body.get("fields");

                GenericPackager packager = getPackager(network);
                ISOMsg msg = new ISOMsg();
                msg.setPackager(packager);
                msg.setMTI(mti);

                if (fields != null) {
                    for (Map.Entry<String, String> e : fields.entrySet()) {
                        try {
                            int de = Integer.parseInt(e.getKey());
                            msg.set(de, e.getValue());
                        } catch (NumberFormatException ignored) { /* skip non-numeric keys */ }
                    }
                }

                byte[] packed = msg.pack();
                String hex = bytesToHex(packed);

                Map<String, Object> out = new HashMap<>();
                out.put("hex",     hex);
                out.put("network", network);
                out.put("mti",     mti);
                out.put("length",  packed.length);

                resp.setStatus(200);
                JSON.writeValue(resp.getWriter(), out);

            } catch (Exception e) {
                LOG.error("Pack error", e);
                resp.setStatus(500);
                Map<String, String> err = new HashMap<>();
                err.put("error", e.getMessage());
                JSON.writeValue(resp.getWriter(), err);
            }
        }
    }

    // ------------------------------------------------------------------
    // /unpack
    // ------------------------------------------------------------------

    static class UnpackServlet extends HttpServlet {
        @Override
        @SuppressWarnings("unchecked")
        protected void doPost(HttpServletRequest req, HttpServletResponse resp)
                throws IOException {
            resp.setContentType("application/json");
            try {
                Map<String, Object> body = JSON.readValue(req.getInputStream(), Map.class);
                String network = (String) body.getOrDefault("network", "visa");
                String hex     = (String) body.get("hex");

                if (hex == null || hex.isBlank()) {
                    resp.setStatus(400);
                    resp.getWriter().write("{\"error\":\"'hex' field is required\"}");
                    return;
                }

                byte[] raw = hexToBytes(hex);
                GenericPackager packager = getPackager(network);
                ISOMsg msg = new ISOMsg();
                msg.setPackager(packager);
                msg.unpack(raw);

                Map<String, String> fields = new HashMap<>();
                for (int i = 0; i <= 128; i++) {
                    if (msg.hasField(i)) {
                        fields.put(String.valueOf(i), msg.getString(i));
                    }
                }

                Map<String, Object> out = new HashMap<>();
                out.put("mti",     msg.getMTI());
                out.put("fields",  fields);
                out.put("network", network);

                resp.setStatus(200);
                JSON.writeValue(resp.getWriter(), out);

            } catch (Exception e) {
                LOG.error("Unpack error", e);
                resp.setStatus(500);
                Map<String, String> err = new HashMap<>();
                err.put("error", e.getMessage());
                JSON.writeValue(resp.getWriter(), err);
            }
        }
    }

    // ------------------------------------------------------------------
    // Hex helpers
    // ------------------------------------------------------------------

    private static String bytesToHex(byte[] bytes) {
        StringBuilder sb = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) {
            sb.append(String.format("%02X", b));
        }
        return sb.toString();
    }

    private static byte[] hexToBytes(String hex) {
        hex = hex.replaceAll("\\s", "");
        int len = hex.length();
        byte[] data = new byte[len / 2];
        for (int i = 0; i < len; i += 2) {
            data[i / 2] = (byte) ((Character.digit(hex.charAt(i), 16) << 4)
                                 + Character.digit(hex.charAt(i + 1), 16));
        }
        return data;
    }
}
