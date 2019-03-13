FROM mesudip/python-nginx:alpine

RUN apk add gcc libc-dev openssl-dev linux-headers libffi-dev
COPY ./requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt && rm -f /requirements.txt
COPY . /app/
WORKDIR /app
CMD ["python3","-u" ,"main.py"]
