# Imagem do app web, usada para publicar no Hugging Face Spaces
# (ou em qualquer servidor que rode Docker).

FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=7860

# O Hugging Face roda o container com o usuário de id 1000, sem ser root.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH
WORKDIR /home/user/app

# Dependências primeiro: o Docker reaproveita esta camada enquanto o
# requirements.txt não mudar, e os builds seguintes ficam bem mais rápidos.
COPY --chown=user requirements.txt .
RUN pip install --user -r requirements.txt

# Baixa o modelo de recorte (~1 GB) durante o build. Assim ele já vem
# dentro da imagem, em vez de ser baixado no primeiro uso do Danilo.
RUN python -c "from rembg import new_session; new_session()"

COPY --chown=user pipeline.py servidor.py main.py ./
COPY --chown=user services ./services
COPY --chown=user web ./web

EXPOSE 7860
CMD ["python", "servidor.py"]
