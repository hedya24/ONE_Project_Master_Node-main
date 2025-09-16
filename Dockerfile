FROM python:3.9-slim
WORKDIR /app
COPY . /app
RUN pip install -r requirements.txt
RUN apt-get update
RUN apt-get install -y redis-tools curl tar gzip
RUN curl -LO https://get.helm.sh/helm-v3.14.4-linux-amd64.tar.gz && tar -zxvf helm-v3.14.4-linux-amd64.tar.gz && mv linux-amd64/helm /usr/local/bin/helm && chmod +x /usr/local/bin/helm && rm -rf helm-v3.14.4-linux-amd64.tar.gz linux-amd64
RUN curl -LO https://dl.k8s.io/release/v1.28.4/bin/linux/amd64/kubectl && install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && rm kubectl
RUN apt-get clean
EXPOSE 5000
CMD ["sh", "-c", "flask run --host=0.0.0.0 & celery -A app.celery worker --loglevel=info --concurrency=1"]
