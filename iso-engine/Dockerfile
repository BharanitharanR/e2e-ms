# iso-engine/Dockerfile — jPOS ISO 8583 sidecar
# Two-stage build: Maven build → slim JRE runtime
FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /build
COPY pom.xml .
# Pre-download dependencies (cache layer)
RUN mvn dependency:go-offline -q
COPY src/ src/
RUN mvn package -DskipTests -q

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM eclipse-temurin:17-jre-alpine
WORKDIR /app
COPY --from=build /build/target/iso-engine-*-SNAPSHOT.jar app.jar

ENV ISO_ENGINE_PORT=8200
EXPOSE 8200

HEALTHCHECK --interval=10s --timeout=5s --retries=6 \
  CMD wget -qO- http://localhost:8200/health || exit 1

ENTRYPOINT ["java", "-jar", "/app/app.jar"]
