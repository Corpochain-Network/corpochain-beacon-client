from __future__ import annotations

import argparse
import binascii
import os
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from corpochain.plotters.bladebit import get_bladebit_install_info, plot_bladebit
from corpochain.plotters.chiapos import get_chiapos_install_info, plot_chia
from corpochain.plotters.madmax import get_madmax_install_info, plot_madmax


class Options(Enum):
    TMP_DIR = 1
    TMP_DIR2 = 2
    FINAL_DIR = 3
    K = 4
    MEMO = 5
    ID = 6
    BUFF = 7
    NUM_BUCKETS = 8
    STRIPE_SIZE = 9
    NUM_THREADS = 10
    NOBITFIELD = 11
    PLOT_COUNT = 12
    MADMAX_NUM_BUCKETS_PHRASE3 = 13
    MADMAX_WAITFORCOPY = 14
    POOLKEY = 15
    FARMERKEY = 16
    MADMAX_TMPTOGGLE = 17
    MADMAX_RMULTI2 = 18
    BLADEBIT_WARMSTART = 19
    BLADEBIT_NONUMA = 20
    VERBOSE = 21
    OVERRIDE_K = 22
    ALT_FINGERPRINT = 23
    EXCLUDE_FINAL_DIR = 24
    CONNECT_TO_DAEMON = 25
    BLADEBIT_NO_CPU_AFFINITY = 26
    BLADEBIT_CACHE = 27
    BLADEBIT_F1_THREAD = 28
    BLADEBIT_FP_THREAD = 29
    BLADEBIT_C_THREAD = 30
    BLADEBIT_P2_THREAD = 31
    BLADEBIT_P3_THREAD = 32
    BLADEBIT_ALTERNATE = 33
    BLADEBIT_NO_T1_DIRECT = 34
    BLADEBIT_NO_T2_DIRECT = 35
    COMPRESSION = 36
    BLADEBIT_DEVICE_INDEX = 37
    CUDA_TMP_DIR = 39
    BLADEBIT_HYBRID_128_MODE = 40
    BLADEBIT_HYBRID_16_MODE = 41
    

chia_plotter_options = [
    Options.TMP_DIR,
    Options.TMP_DIR2,
    Options.K,
    Options.MEMO,
    Options.ID,
    Options.BUFF,
    Options.NUM_BUCKETS,
    Options.STRIPE_SIZE,
    Options.NUM_THREADS,
    Options.NOBITFIELD,
    Options.OVERRIDE_K,
    Options.ALT_FINGERPRINT,
    Options.FARMERKEY,
    Options.POOLKEY,
    Options.PLOT_COUNT,
    Options.EXCLUDE_FINAL_DIR,
    Options.CONNECT_TO_DAEMON,
    Options.FINAL_DIR,
    Options.COMPRESSION,
]

madmax_plotter_options = [
    Options.K,
    Options.PLOT_COUNT,
    Options.NUM_THREADS,
    Options.NUM_BUCKETS,
    Options.MADMAX_NUM_BUCKETS_PHRASE3,
    Options.TMP_DIR,
    Options.TMP_DIR2,
    Options.MADMAX_WAITFORCOPY,
    Options.POOLKEY,
    Options.FARMERKEY,
    Options.MADMAX_TMPTOGGLE,
    Options.MADMAX_RMULTI2,
    Options.CONNECT_TO_DAEMON,
    Options.FINAL_DIR,
]

bladebit_cuda_plotter_options = [
    Options.NUM_THREADS,
    Options.PLOT_COUNT,
    Options.FARMERKEY,
    Options.POOLKEY,
    Options.CUDA_TMP_DIR,
    Options.ID,
    Options.BLADEBIT_WARMSTART,
    Options.BLADEBIT_NONUMA,
    Options.BLADEBIT_NO_CPU_AFFINITY,
    Options.VERBOSE,
    Options.CONNECT_TO_DAEMON,
    Options.FINAL_DIR,
    Options.COMPRESSION,
    Options.BLADEBIT_DEVICE_INDEX,
    Options.BLADEBIT_HYBRID_128_MODE,
    Options.BLADEBIT_HYBRID_16_MODE,
]

bladebit_ram_plotter_options = [
    Options.NUM_THREADS,
    Options.PLOT_COUNT,
    Options.FARMERKEY,
    Options.POOLKEY,
    Options.ID,
    Options.BLADEBIT_WARMSTART,
    Options.BLADEBIT_NONUMA,
    Options.BLADEBIT_NO_CPU_AFFINITY,
    Options.VERBOSE,
    Options.CONNECT_TO_DAEMON,
    Options.FINAL_DIR,
    Options.COMPRESSION,
]

bladebit_disk_plotter_options = [
    Options.NUM_THREADS,
    Options.PLOT_COUNT,
    Options.FARMERKEY,
    Options.POOLKEY,
    Options.ID,
    Options.BLADEBIT_WARMSTART,
    Options.BLADEBIT_NONUMA,
    Options.VERBOSE,
    Options.CONNECT_TO_DAEMON,
    Options.FINAL_DIR,
    Options.BLADEBIT_NO_CPU_AFFINITY,
    Options.BLADEBIT_CACHE,
    Options.BLADEBIT_F1_THREAD,
    Options.BLADEBIT_FP_THREAD,
    Options.BLADEBIT_C_THREAD,
    Options.BLADEBIT_P2_THREAD,
    Options.BLADEBIT_P3_THREAD,
    Options.BLADEBIT_ALTERNATE,
    Options.TMP_DIR,
    Options.TMP_DIR2,
    Options.NUM_BUCKETS,
    Options.MEMO,
    Options.BLADEBIT_NO_T1_DIRECT,
    Options.BLADEBIT_NO_T2_DIRECT,
    Options.COMPRESSION,
]


def get_plotters_root_path(root_path: Path) -> Path:
    return root_path / "plotters"


def build_parser(subparsers, root_path, option_list, name, plotter_desc):
    parser = subparsers.add_parser(name, help=plotter_desc)
    for option in option_list:
        if option is Options.K:
            parser.add_argument(
                "-k",
                "--size",
                type=int,
                help="K value.",
                default=32,
            )
        if option is Options.NUM_BUCKETS:
            u_default = 0 if name == "chiapos" else 256
            parser.add_argument(
                "-u",
                "--buckets",
                type=int,
                help="Number of buckets.",
                default=u_default,
            )
        if option is Options.STRIPE_SIZE:
            parser.add_argument(
                "-s",
                "--stripes",
                type=int,
                help="Stripe size.",
                default=0,
            )
        if option is Options.TMP_DIR:
            parser.add_argument(
                "-t",
                "--tmp_dir",
                type=str,
                dest="tmpdir",
                help="Temporary directory 1.",
                required=True,
            )
        if option is Options.TMP_DIR2:
            parser.add_argument(
                "-2",
                "--tmp_dir2",
                type=str,
                dest="tmpdir2",
                help="Temporary directory 2.",
                default="",
            )
        if option is Options.CUDA_TMP_DIR:
            parser.add_argument(
                "-t",
                "--tmp_dir",
                type=str,
                dest="tmpdir",
                help="Temporary directory 1.",
                required=False,  # Unlike `Options.TMP_DIR`, this is not required
            )
        if option is Options.FINAL_DIR:
            parser.add_argument(
                "-d",
                "--final_dir",
                type=str,
                dest="finaldir",
                help="Final directory.",
                required=True,
            )
        if option is Options.BUFF:
            parser.add_argument(
                "-b",
                "--buffer",
                type=int,
                help="Size of the buffer, in MB.",
                default=0,
            )
        if option is Options.NUM_THREADS:
            r_default = 4 if name == "madmax" else 0
            parser.add_argument(
                "-r",
                "--threads",
                type=int,
                help="Num threads.",
                default=r_default,
            )
        if option is Options.NOBITFIELD:
            parser.add_argument(
                "-e",
                "--nobitfield",
                action="store_true",
                help="Disable bitfield.",
                default=False,
            )
        if option is Options.MEMO:
            parser.add_argument(
                "-m",
                "--memo",
                type=binascii.unhexlify,
                help="Memo variable.",
            )
        if option is Options.ID:
            parser.add_argument(
                "-i",
                "--id",
                type=binascii.unhexlify,
                help="Plot id",
            )
        if option is Options.PLOT_COUNT:
            parser.add_argument(
                "-n",
                "--count",
                type=int,
                help="Number of plots to create (default = 1)",
                default=1,
            )
        if option is Options.MADMAX_NUM_BUCKETS_PHRASE3:
            parser.add_argument(
                "-v",
                "--buckets3",
                type=int,
                help="Number of buckets for phase 3+4 (default = 256)",
                default=256,
            )
        if option is Options.MADMAX_WAITFORCOPY:
            parser.add_argument(
                "-w",
                "--waitforcopy",
                action="store_true",
                help="Wait for copy to start next plot",
                default=False,
            )
        if option is Options.MADMAX_TMPTOGGLE:
            parser.add_argument(
                "-G",
                "--tmptoggle",
                action="store_true",
                help="Alternate tmpdir/tmpdir2 (default = false)",
                default=False,
            )
        if option is Options.MADMAX_RMULTI2:
            parser.add_argument(
                "-K",
                "--rmulti2",
                type=int,
                help="Thread multiplier for P2 (default = 1)",
                default=1,
            )
        if option is Options.POOLKEY:
            parser.add_argument(
                "-p",
                "--pool-key",
                type=binascii.unhexlify,
                help="Pool Public Key (48 bytes)",
                default="",
            )
        if option is Options.FARMERKEY:
            parser.add_argument(
                "-f",
                "--farmerkey",
                type=binascii.unhexlify,
                help="Farmer Public Key (48 bytes)",
                default="",
            )
        if option is Options.BLADEBIT_WARMSTART:
            parser.add_argument(
                "-w",
                "--warmstart",
                action="store_true",
                help="Warm start",
                default=False,
            )
        if option is Options.BLADEBIT_NONUMA:
            parser.add_argument(
                "--nonuma",
                action="store_true",
                help="Disable numa",
                default=False,
            )
        if option is Options.VERBOSE:
            parser.add_argument(
                "-v",
                "--verbose",
                action="store_true",
                help="Set verbose",
                default=False,
            )
        if option is Options.OVERRIDE_K:
            parser.add_argument(
                "--override-k",
                dest="override",
                action="store_true",
                help="Force size smaller than 32",
                default=False,
            )
        if option is Options.ALT_FINGERPRINT:
            parser.add_argument(
                "-a",
                "--alt_fingerprint",
                type=int,
                default=None,
                help="Enter the alternative fingerprint of the key you want to use",
            )
        if option is Options.EXCLUDE_FINAL_DIR:
            parser.add_argument(
                "-x",
                "--exclude_final_dir",
                action="store_true",
                help="Skips adding [final dir] to harvester for farming",
                default=False,
            )
        if option is Options.CONNECT_TO_DAEMON:
            parser.add_argument(
                "-D",
                "--connect-to-daemon",
                action="store_true",
                help=argparse.SUPPRESS,
                default=False,
            )
        if option is Options.BLADEBIT_NO_CPU_AFFINITY:
            parser.add_argument(
                "--no-cpu-affinity",
                action="store_true",
                help="Disable assigning automatic thread affinity",
                default=False,
            )
        if option is Options.BLADEBIT_CACHE:
            parser.add_argument(
                "--cache",
                type=str,
                help="Size of cache to reserve for I/O",
            )
        if option is Options.BLADEBIT_F1_THREAD:
            parser.add_argument(
                "--f1-threads",
                type=int,
                help="Override the thread count for F1 generation",
            )
        if option is Options.BLADEBIT_FP_THREAD:
            parser.add_argument(
                "--fp-threads",
                type=int,
                help="Override the thread count for forward propagation",
            )
        if option is Options.BLADEBIT_C_THREAD:
            parser.add_argument(
                "--c-threads",
                type=int,
                help="Override the thread count for C table processing",
            )
        if option is Options.BLADEBIT_P2_THREAD:
            parser.add_argument(
                "--p2-threads",
                type=int,
                help="Override the thread count for Phase 2",
            )
        if option is Options.BLADEBIT_P3_THREAD:
            parser.add_argument(
                "--p3-threads",
                type=int,
                help="Override the thread count for Phase 3",
            )
        if option is Options.BLADEBIT_ALTERNATE:
            parser.add_argument(
                "--alternate",
                action="store_true",
                help="Halves the temp2 cache size requirements by alternating bucket writing methods between tables",
                default=False,
            )
        if option is Options.BLADEBIT_NO_T1_DIRECT:
            parser.add_argument(
                "--no-t1-direct",
                action="store_true",
                help="Disable direct I/O on the temp 1 directory",
                default=False,
            )
        if option is Options.BLADEBIT_NO_T2_DIRECT:
            parser.add_argument(
                "--no-t2-direct",
                action="store_true",
                help="Disable direct I/O on the temp 2 directory",
                default=False,
            )
        if option is Options.COMPRESSION:
            parser.add_argument(
                "--compress",
                type=int,
                help="Compression level",
                default=1,
            )
        if option is Options.BLADEBIT_DEVICE_INDEX:
            parser.add_argument(
                "--device",
                type=int,
                help="The CUDA device index",
                default=0,
            )
        if option is Options.BLADEBIT_HYBRID_128_MODE:
            parser.add_argument(
                "--disk-128",
                action="store_true",
                help="Enable hybrid disk plotting for 128G system RAM",
                default=False,
            )
        if option is Options.BLADEBIT_HYBRID_16_MODE:
            parser.add_argument(
                "--disk-16",
                action="store_true",
                help="Enable hybrid disk plotting for 16G system RAM",
                default=False,
            )


def call_plotters(root_path: Path, args):
    # Add `plotters` section in CORPOCHAIN_ROOT.
    corpochain_root_path = root_path
    root_path = get_plotters_root_path(root_path)
    if not root_path.is_dir():
        if os.path.exists(root_path):
            try:
                os.remove(root_path)
            except Exception as e:
                print(f"Exception deleting old root path: {type(e)} {e}.")

    if not os.path.exists(root_path):
        print(f"Creating plotters folder within CORPOCHAIN_ROOT: {root_path}")
        try:
            os.mkdir(root_path)
        except Exception as e:
            print(f"Cannot create plotters root path {root_path} {type(e)} {e}.")

    plotters = argparse.ArgumentParser("corpochain plotters", description="Available options.")
    subparsers = plotters.add_subparsers(help="Available options", dest="plotter")

    build_parser(subparsers, root_path, chia_plotter_options, "chiapos", "Create a plot with the chiapos")
    build_parser(subparsers, root_path, madmax_plotter_options, "madmax", "Create a plot with madMAx")

    bladebit_parser = subparsers.add_parser("bladebit", help="Create a plot with bladebit")
    subparsers_bb = bladebit_parser.add_subparsers(dest="plot_type", required=True)
    build_parser(subparsers_bb, root_path, bladebit_cuda_plotter_options, "cudaplot", "Creat a plot using CUDA")
    build_parser(subparsers_bb, root_path, bladebit_ram_plotter_options, "ramplot", "Create a plot using RAM")
    build_parser(subparsers_bb, root_path, bladebit_disk_plotter_options, "diskplot", "Create a plot using disk")

    args = plotters.parse_args(args)

    if args.plotter is None:
        plotters.print_help()
    elif args.plotter == "chiapos":
        plot_chia(args, corpochain_root_path)
    elif args.plotter == "madmax":
        plot_madmax(args, corpochain_root_path, root_path)
    elif args.plotter == "bladebit":
        plot_bladebit(args, corpochain_root_path, root_path)
    elif args.plotter == "version":
        show_plotters_version(corpochain_root_path)


def get_available_plotters(root_path) -> Dict[str, Any]:
    plotters_root_path: Path = get_plotters_root_path(root_path)
    plotters: Dict[str, Any] = {}
    chiapos: Optional[Dict[str, Any]] = get_chiapos_install_info()
    bladebit: Optional[Dict[str, Any]] = get_bladebit_install_info(plotters_root_path)
    madmax: Optional[Dict[str, Any]] = get_madmax_install_info(plotters_root_path)

    if chiapos is not None:
        plotters["chiapos"] = chiapos
    if bladebit is not None:
        plotters["bladebit"] = bladebit
    if madmax is not None:
        plotters["madmax"] = madmax

    return plotters


def show_plotters_version(root_path: Path):
    info = get_available_plotters(root_path)
    if "chiapos" in info and "version" in info["chiapos"]:
        print(f"chiapos: {info['chiapos']['version']}")
    if "bladebit" in info and "version" in info["bladebit"]:
        if info["bladebit"]["cuda_support"]:
            print(f"bladebit: {info['bladebit']['version']} (CUDA ready)")
        else:
            print(f"bladebit: {info['bladebit']['version']}")
    if "madmax" in info and "version" in info["madmax"]:
        print(f"madmax: {info['madmax']['version']}")
