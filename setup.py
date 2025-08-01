from __future__ import annotations

import os
import sys

from setuptools import find_packages, setup

if sys.platform == "win32":
    chiavdf_version = "chiavdf==1.1.8"
else:
    chiavdf_version = "chiavdf==1.1.0"

dependencies = [
    "aiofiles==23.2.1",  # Async IO for files
    "anyio==4.0.0",
    "blspy==2.0.2", # Signature library
    chiavdf_version,  # timelord and vdf verification
    "chiapos==2.0.3",  # proof of space
    "aiohttp==3.9.1",  # HTTP server for beacon client rpc
    "aiosqlite==0.10.0",  # asyncio wrapper for sqlite, to store blocks
    "bitstring==4.1.2",  # Binary data management library
    "colorama==0.4.6",  # Colorizes terminal output
    "colorlog==6.7.0",  # Adds color to logs
    "concurrent-log-handler==0.9.24",  # Concurrently log and rotate logs
    "cryptography==41.0.5",  # Python cryptography library for TLS - keyring conflict
    "filelock==3.13.1",  # For reading and writing config multiprocess and multithread safely  (non-reentrant locks)
    "keyring==24.3.0",  # Store keys in MacOS Keychain, Windows Credential Locker
    "PyYAML==6.0.1",  # Used for config file format
    "setproctitle==1.3.3",  # Gives the corpochain processes readable names
    "sortedcontainers==2.4.0",  # For maintaining sorted mempools
    "click==8.1.3",  # For the CLI
    "dnspython==2.4.2",  # Query DNS seeds
    "watchdog==2.2.0",  # Filesystem event watching - watches keyring.yaml
    "dnslib==0.9.23",  # dns lib
    "typing-extensions==4.8.0",  # typing backports like Protocol and TypedDict
    "zstd==1.5.5.1",
    "packaging==23.2",
    "psutil==5.9.4",
    "web3==6.14.0",
    "PyJWT==2.8.0",
]

upnp_dependencies = [
    "miniupnpc==2.2.2",  # Allows users to open ports on their router
]

dev_dependencies = [
    "build",
    "coverage",
    "diff-cover",
    "pre-commit",
    "py3createtorrent",
    "pylint",
    "pytest",
    # TODO: do not checkpoint to main
    "pytest-asyncio==0.20.3",  # require attribute 'fixture'
    "pytest-cov",
    "pytest-monitor; sys_platform == 'linux'",
    "pytest-xdist",
    "twine",
    "isort",
    "flake8",
    "mypy",
    "black==22.10.0",
    "aiohttp_cors",  # For blackd
    "ipython",  # For asyncio debugging
    "pyinstaller==5.8.0",
    "types-aiofiles",
    "types-cryptography",
    "types-pyyaml",
    "types-setuptools",
]

kwargs = dict(
    name="corpochain-beacon-client",
    author="Corpochain",
    author_email="hello@corpochain.pl",
    description="Corpochain Beacon Client",
    url="https:/corpochain.pl/",
    license="Apache License",
    python_requires=">=3.7, <4",
    keywords="corpochain blockchain node beacon",
    install_requires=dependencies,
    extras_require=dict(
        dev=dev_dependencies,
        upnp=upnp_dependencies,
    ),
    packages=find_packages(include=["build_scripts", "corpochain", "corpochain.*", "mozilla-ca"]),
    entry_points={
        "console_scripts": [
            "corpochain = corpochain.cmds.corpochain:main",
            "corpochain_daemon = corpochain.daemon.server:main",
            "corpochain_beacon = corpochain.server.start_beacon:main",
            "corpochain_harvester = corpochain.server.start_harvester:main",
            "corpochain_farmer = corpochain.server.start_farmer:main",
            "corpochain_introducer = corpochain.server.start_introducer:main",
            "corpochain_crawler = corpochain.seeder.start_crawler:main",
            "corpochain_seeder = corpochain.seeder.dns_server:main",
            "corpochain_timelord = corpochain.server.start_timelord:main",
            "corpochain_timelord_launcher = corpochain.timelord.timelord_launcher:main",
        ]
    },
    package_data={
        "corpochain": ["pyinstaller.spec"],
        "": ["py.typed"],
        "corpochain.util": ["initial-*.yaml", "english.txt"],
        "corpochain.ssl": ["corpochain_ca.crt", "corpochain_ca.key"],
        "mozilla-ca": ["cacert.pem"],
    },
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    zip_safe=False,
    project_urls={
        "Source": "https://github.com/Corpochain-Network/corpochain-beacon-client/",
    },
)

if "setup_file" in sys.modules:
    # include dev deps in regular deps when run in snyk
    dependencies.extend(dev_dependencies)

if len(os.environ.get("CORPOCHAIN_SKIP_SETUP", "")) < 1:
    setup(**kwargs)  # type: ignore
