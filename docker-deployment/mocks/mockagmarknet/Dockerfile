FROM golang:1.26.1-bookworm AS builder
WORKDIR /src
COPY go.mod ./
COPY main.go ./
RUN CGO_ENABLED=0 go build -o mockagmarknet .

FROM cgr.dev/chainguard/wolfi-base:latest
WORKDIR /app
COPY --from=builder /src/mockagmarknet .
EXPOSE 9101
ENTRYPOINT ["./mockagmarknet"]
