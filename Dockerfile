# mesudip/python-nginx:alpine is merge of official python and nginx images.
FROM mesudip/python-nginx:alpine

RUN pip install --upgrade pip

HEALTHCHECK --interval=10s --timeout=2s --start-period=10s --retries=3 CMD pgrep nginx &&  pgrep python3 >> /dev/null  || exit 1
VOLUME  ["/etc/nginx/dhparam", "/tmp/acme-challenges/","/etc/nginx/conf.d","/etc/nginx/ssl"]
CMD ["sh","-e" ,"/docker-entrypoint.sh"]
COPY ./requirements.txt /requirements.txt
RUN apk --no-cache add  openssl && \
    apk add --no-cache --virtual .build-deps \
    gcc libc-dev openssl-dev linux-headers libffi-dev && \
    pip install --no-cache-dir -r /requirements.txt &&  \
    rm -f /requirements.txt && apk del .build-deps && \
    ln -s /app/getssl /bin/getssl && ln -s /app/verify /bin/verify && \
    ln -s /app/docker-entrypoint.sh /docker-entrypoint.sh
ARG LETSENCRYPT_API="https://acme-v02.api.letsencrypt.org/directory"
ENV LETSENCRYPT_API=${LETSENCRYPT_API} \
    CHALLENGE_DIR=/tmp/acme-challenges/ \
    DHPARAM_SIZE=2048 \
    CLIENT_MAX_BODY_SIZE=1m \
    DEFAULT_HOST=true
WORKDIR /app
COPY . /app/