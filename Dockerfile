FROM mesudip/python-nginx-alpine

RUN apk add gcc libc-dev openssl-dev linux-headers libffi-dev
COPY ./requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt && rm -f /requirements.txt
WORKDIR /app
COPY docker/entry-point.sh /entry-point.sh
COPY . /app
CMD ["/bin/ash", "/entry-point.sh"]
