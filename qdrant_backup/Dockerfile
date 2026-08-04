FROM alpine:3.20

RUN apk add --no-cache curl openssl

COPY backup.sh /backup.sh
RUN chmod +x /backup.sh

ENTRYPOINT ["/backup.sh"]
