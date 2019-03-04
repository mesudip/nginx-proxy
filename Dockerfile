FROM python:3.6.7-alpine
COPY ./requirements.txt /requirements.txt
RUN pip install -r /requirements.txt && rm -f /requirements.txt
WORKDIR /app
COPY . /app
CMD ["python3", "main.py"]
