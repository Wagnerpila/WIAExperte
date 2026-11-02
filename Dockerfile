# Etapa 1: compila o LibreDWG (dwg2dxf), usado para ler os textos dos desenhos .dwg.
# Não há pacote no Debian estável; a compilação fica em cache entre os deploys.
FROM python:3.11-slim AS libredwg
ARG LIBREDWG_VERSION=0.13.4
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential pkg-config curl xz-utils ca-certificates \
 && curl -fsSL "https://ftp.gnu.org/gnu/libredwg/libredwg-${LIBREDWG_VERSION}.tar.xz" | tar -xJ -C /tmp \
 && cd "/tmp/libredwg-${LIBREDWG_VERSION}" \
 && ./configure --prefix=/opt/libredwg --disable-bindings --disable-docs --disable-static \
 && make -j"$(nproc)" \
 && make install-strip

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WIA_DATA_DIR=/data \
    PATH=/opt/libredwg/bin:$PATH LD_LIBRARY_PATH=/opt/libredwg/lib \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /srv

COPY --from=libredwg /opt/libredwg /opt/libredwg

COPY requirements.txt .
# Chromium (só o modo sem tela) para a integração com o GreenDocs. As bibliotecas vão pelo apt do
# Debian 13: o "--with-deps" do Playwright procura pacotes do Ubuntu que não existem aqui.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 \
    libatspi2.0-0t64 libcups2t64 libdrm2 libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2t64 libpango-1.0-0 libcairo2 libxshmfence1 fonts-liberation \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir -r requirements.txt \
 && playwright install --only-shell chromium

COPY app ./app
COPY prompts ./prompts

RUN useradd --create-home --uid 1000 wia && mkdir -p /data && chown wia:wia /data
USER wia
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/saude')"
# Um worker só: o limite de tentativas de login fica em memória e o SQLite prefere poucos escritores.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
