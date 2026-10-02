# Imagem do app web. Na VPS ela roda junto com o Caddy (ver docker-compose.yml),
# que cuida do HTTPS.

FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8000

# Fonte com acentos para o selo de avaliação. A imagem "slim" não traz
# nenhuma fonte, e a reserva do Pillow não tem "ç" nem "õ" (o selo saía
# "avalia□□es").
RUN apt-get update     && apt-get install -y --no-install-recommends fonts-dejavu-core     && rm -rf /var/lib/apt/lists/*

# Roda com um usuário comum (id 1000), não como root: se alguém achar uma
# falha no app, não ganha controle total do container.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH
WORKDIR /home/user/app

# Dependências primeiro: o Docker reaproveita esta camada enquanto o
# requirements.txt não mudar, e os builds seguintes ficam bem mais rápidos.
COPY --chown=user requirements.txt .
RUN pip install --user -r requirements.txt

COPY --chown=user services ./services

# Baixa o modelo de recorte durante o build. Assim ele já vem dentro da
# imagem, em vez de ser baixado no primeiro uso do Danilo.
RUN python -c "from services.image_service import ImageService; ImageService('/tmp').preparar_modelo()"

COPY --chown=user pipeline.py servidor.py main.py ./
COPY --chown=user web ./web

EXPOSE 8000
CMD ["python", "servidor.py"]
