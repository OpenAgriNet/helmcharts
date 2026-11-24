FROM golang:1.26.1-bookworm AS builder
WORKDIR /src
COPY go.mod ./
COPY main.go ./
COPY data.go ./
RUN CGO_ENABLED=0 go build -o mockkhc .

FROM cgr.dev/chainguard/wolfi-base:latest
WORKDIR /app
COPY --from=builder /src/mockkhc .
EXPOSE 9106
ENTRYPOINT ["./mockkhc"]
