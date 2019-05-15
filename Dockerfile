# mesudip/python-nginx:alpine is merge of official python and nginx images.
FROM mesudip/python-nginx:alpine
ARG LETSENCRYPT_API="https://acme-v02.api.letsencrypt.org/directory"
RUN apk add gcc libc-dev openssl-dev linux-headers libffi-dev
COPY ./requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt && rm -f /requirements.txt
ENV LETSENCRYPT_API=${LETSENCRYPT_API}
COPY . /app/
WORKDIR /app
CMD ["python3","-u" ,"main.py"]
