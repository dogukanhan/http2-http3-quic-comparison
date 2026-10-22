docker build -f server/https/Dockerfile -t app-https .
docker run --rm -p 8443:8443 app-https
