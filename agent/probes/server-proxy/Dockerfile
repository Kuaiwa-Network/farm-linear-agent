FROM scratch
COPY --chmod=0555 server-proxy /server-proxy
USER 65534:65534
ENTRYPOINT ["/server-proxy"]
