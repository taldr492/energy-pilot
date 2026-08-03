ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

RUN apk add --no-cache python3 py3-pip && \
    pip3 install --no-cache-dir --break-system-packages \
      fastapi==0.116.1 \
      uvicorn==0.35.0 \
      pydantic==2.11.7

COPY app /app
COPY run.sh /run.sh
RUN chmod 755 /run.sh

CMD ["/run.sh"]
