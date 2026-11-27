FROM ubuntu:20.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       build-essential ca-certificates python-is-python2 python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir matplotlib==3.7.5

WORKDIR /simulator
COPY . .
RUN ./waf configure --build-profile=optimized --disable-python \
    && ./waf build

ENV MPLBACKEND=Agg MPLCONFIGDIR=/tmp/matplotlib
CMD ["bash"]
