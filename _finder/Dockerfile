FROM --platform=${TARGETPLATFORM} ghcr.io/alsosee/codegen:latest as codegen

FROM --platform=${TARGETPLATFORM} golang:1.21 as build-env

WORKDIR /src/

# finder directory is checked out by GitHub Actions
ADD ./finder /src/finder

# _finder directory exist in the current repo
ADD ./_finder /src/_finder

# override templates
RUN cp ./_finder/templates/* /src/finder/templates/

COPY --from=codegen /usr/local/bin/codegen /usr/local/bin/codegen
RUN codegen -in /src/_finder/schema.yml -out /src/finder/structs/content.gen.go

RUN cd /src/finder && go test -mod=vendor -cover ./...

ARG TARGETOS
ARG TARGETARCH
RUN cd /src/finder && \
    CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} \
    go build -ldflags="-w -s" -mod=vendor -o /go/bin/app /src/finder/


FROM --platform=${TARGETPLATFORM} gcr.io/distroless/static:966f4bd97f611354c4ad829f1ed298df9386c2ec
# latest-amd64 -> 966f4bd97f611354c4ad829f1ed298df9386c2ec
# https://github.com/GoogleContainerTools/distroless/tree/master/base

ENV INPUT_TEMPLATES=/templates
ENV INPUT_STATIC=/static

COPY finder/templates /templates
COPY finder/static /static
COPY --from=build-env /go/bin/app /app

CMD ["/app"]
