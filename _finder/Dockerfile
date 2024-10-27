FROM --platform=${TARGETPLATFORM} ghcr.io/alsosee/codegen:latest AS codegen

FROM --platform=${TARGETPLATFORM} golang:1.21 AS build-env

WORKDIR /src/

# finder directory is checked out by GitHub Actions
COPY ./finder /src/finder

# _finder directory exist in the current repo
COPY ./_finder /src/_finder

COPY --from=codegen /usr/local/bin/codegen /usr/local/bin/codegen

# override templates
RUN cp ./_finder/templates/* /src/finder/templates/ && \
    # generate content struct
    codegen -in /src/_finder/schema.yml -out /src/finder/structs/content.gen.go && \
    # test finder
    cd /src/finder && go test -mod=vendor -cover ./...

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

COPY --from=build-env /src/finder/templates /templates
COPY --from=build-env /src/finder/static /static
COPY --from=build-env /src/finder/functions /static/functions
COPY --from=build-env /go/bin/app /app

CMD ["/app"]
