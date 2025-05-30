#!/usr/bin/env python3
# Copyright 2024 ETH Zurich and University of Bologna.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
#
# Tim Fischer <fischeti@iis.ee.ethz.ch>

import random
import argparse
import os
import math

MEM_SIZE = 2**16
NUM_X = 4
NUM_Y = 4
HBM_BASE_ADDR = 0x80000000

data_widths = {"wide": 512, "narrow": 64}

random.seed(42)


def clog2(x: int):
    """Compute the ceiling of the log2 of x."""
    return (x - 1).bit_length()


def get_xy_base_addr(x: int, y: int):
    """Get the address of a tile in the mesh."""
    assert x <= NUM_X+1 and y <= NUM_Y+1
    return (x * NUM_Y + y) * MEM_SIZE

def get_hbm_base_addr(ch: int):
    """Get the address of an HBM channel."""
    assert ch <= NUM_Y+1
    return HBM_BASE_ADDR + (ch << MEM_SIZE)


def gen_job_str(
    length: int,
    src_addr: int,
    dst_addr: int,
    *,
    max_src_burst_size: int = 256,
    max_dst_burst_size: int = 256,
    r_aw_decouple: bool = False,
    r_w_decouple: bool = False,
    num_errors: int = 0,
):
    # pylint: disable=too-many-arguments
    """Generate a single job."""
    job_str = ""
    job_str += f"{int(length)}\n"
    job_str += f"{hex(src_addr)}\n"
    job_str += f"{hex(dst_addr)}\n"
    job_str += f"{0}\n" # src_protocol: AXI
    job_str += f"{0}\n" # dst_protocol: AXI
    job_str += f"{max_src_burst_size}\n"
    job_str += f"{max_dst_burst_size}\n"
    job_str += f"{int(r_aw_decouple)}\n"
    job_str += f"{int(r_w_decouple)}\n"
    job_str += f"{num_errors}\n"
    return job_str


def emit_jobs(jobs, out_dir, name, idx):
    """Emit jobs to file."""
    # Generate directory if it does not exist
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)
    with open(f"{out_dir}/{name}_{idx}.txt", "w", encoding="utf-8") as job_file:
        job_file.write(jobs)
        job_file.close()


def gen_chimney2chimney_traffic(
    narrow_burst_length: int = 16,
    num_narrow_bursts: int = 16,
    rw: str = "write",
    bidir: bool = False,
    out_dir: str = "jobs"
):
    """Generate Chimney to Chimney traffic."""
    num_masters = 2
    for i in range(num_masters):
        jobs = ""
        if bidir or i == 0:
            for _ in range(num_narrow_bursts):
                length = narrow_burst_length * data_widths["narrow"] / 8
                assert length <= MEM_SIZE
                src_addr = 0 if rw == "write" else MEM_SIZE
                dst_addr = MEM_SIZE if rw == "write" else 0
                job_str = gen_job_str(length, src_addr, dst_addr)
                jobs += job_str
        emit_jobs(jobs, out_dir, "chimney2chimney", i)


def gen_nw_chimney2chimney_traffic(
    narrow_burst_length: int,
    wide_burst_length: int,
    num_narrow_bursts: int,
    num_wide_bursts: int,
    rw: str,
    bidir: bool,
    out_dir: str
):
    # pylint: disable=too-many-arguments, too-many-positional-arguments
    """Generate Narrow Wide Chimney to Chimney traffic."""
    num_masters = 2
    for i in range(num_masters):
        wide_jobs = ""
        narrow_jobs = ""
        wide_length = wide_burst_length * data_widths["wide"] / 8
        narrow_length = narrow_burst_length * data_widths["narrow"] / 8
        assert wide_length <= MEM_SIZE and narrow_length <= MEM_SIZE
        src_addr = 0 if rw == "write" else MEM_SIZE
        dst_addr = MEM_SIZE if rw == "write" else 0
        if bidir or i == 0:
            for _ in range(num_wide_bursts):
                wide_jobs += gen_job_str(wide_length, src_addr, dst_addr)
            for _ in range(num_narrow_bursts):
                narrow_jobs += gen_job_str(narrow_length, src_addr, dst_addr)
        emit_jobs(wide_jobs, out_dir, "nw_chimney2chimney", i)
        emit_jobs(narrow_jobs, out_dir, "nw_chimney2chimney", i + 100)


def gen_mesh_traffic(
    narrow_burst_length: int,
    wide_burst_length: int,
    num_narrow_bursts: int,
    num_wide_bursts: int,
    rw: str,
    traffic_type: str,
    out_dir: str,
    **_kwargs
):
    # pylint: disable=too-many-arguments, too-many-locals, too-many-branches, too-many-statements, too-many-positional-arguments
    """Generate Mesh traffic."""
    for x in range(0, NUM_X):
        for y in range(0, NUM_Y):
            wide_jobs = ""
            narrow_jobs = ""
            wide_length = wide_burst_length * data_widths["wide"] / 8
            narrow_length = narrow_burst_length * data_widths["narrow"] / 8
            local_addr = get_xy_base_addr(x, y)
            assert wide_length <= MEM_SIZE and narrow_length <= MEM_SIZE
            if traffic_type == "hbm":
                # Tile x=0 are the HBM channels
                # Each core read from the channel of its y coordinate
                ext_addr = get_hbm_base_addr(y)
                accesses = [(ext_addr, rw, wide_length)]
            elif traffic_type == "uniform":
                ext_addr = local_addr
                while ext_addr == local_addr:
                    ext_addr = get_xy_base_addr(random.randint(0, NUM_X-1),
                                                random.randint(0, NUM_Y-1))
                accesses = [(ext_addr, rw, wide_length)]
            elif traffic_type == "onehop":
                if not (x == 0 and y == 0):
                    wide_length = 0
                    narrow_length = 0
                    local_addr = 0
                    ext_addr = 0
                else:
                    ext_addr = get_xy_base_addr(x, y + 1)
                accesses = [(ext_addr, rw, wide_length)]

            elif traffic_type == "bit_complement":
                ext_addr = get_xy_base_addr(NUM_X - x - 1, NUM_Y - y - 1)
                accesses = [(ext_addr, rw, wide_length)]
            elif traffic_type == "bit_reverse":
                # in order to achieve same result as garnet:
                # change to space where addresses start at 0 and return afterwards
                straight = x * NUM_Y + y
                num_destinations = NUM_X * NUM_Y
                reverse = straight & 1  # LSB
                num_bits = clog2(num_destinations)
                for _ in range(1, num_bits):
                    reverse <<= 1
                    straight >>= 1
                    reverse |= (straight & 1)  # LSB
                ext_addr = get_xy_base_addr(reverse % NUM_X, reverse // NUM_X)
                accesses = [(ext_addr, rw, wide_length)]
            elif traffic_type == "bit_rotation":
                source = x * NUM_Y + y
                num_destinations = NUM_X * NUM_Y
                if source % 2 == 0:
                    ext = source // 2
                else:  # (source % 2 == 1)
                    ext = (source // 2) + (num_destinations // 2)
                ext_addr = get_xy_base_addr(ext % NUM_X, ext // NUM_X)
                accesses = [(ext_addr, rw, wide_length)]
            elif traffic_type == "neighbor":
                ext_addr = get_xy_base_addr((x + 1) % NUM_X, y)
                accesses = [(ext_addr, rw, wide_length)]
            elif traffic_type == "shuffle":
                source = x * NUM_Y + y
                num_destinations = NUM_X * NUM_Y
                if source < num_destinations // 2:
                    ext = source * 2
                else: ext = (source * 2) - num_destinations + 1
                ext_addr = get_xy_base_addr(ext % NUM_X, ext // NUM_X)
                accesses = [(ext_addr, rw, wide_length)]
            elif traffic_type == "transpose":
                if NUM_X == NUM_Y:
                    dest_x = y
                    dest_y = x
                elif NUM_Y > NUM_X:
                    assert NUM_Y % NUM_X == 0, "NUM_Y must be divisible by NUM_X"
                    dest_x = y - (y // NUM_X) * NUM_X
                    dest_y = x + (y // NUM_X) * NUM_X
                else:
                    assert NUM_X % NUM_Y == 0, "NUM_X must be divisible by NUM_Y"
                    dest_x = y + (x // NUM_Y) * NUM_Y
                    dest_y = x - (x // NUM_Y) * NUM_Y
                ext_addr = get_xy_base_addr(dest_x, dest_y)
                accesses = [(ext_addr, rw, wide_length)]
            elif traffic_type == "tornado":
                dest_x = (x + math.ceil(NUM_X / 2) - 1) % NUM_X
                ext_addr = get_xy_base_addr(dest_x, y)
                accesses = [(ext_addr, rw, wide_length)]
            elif traffic_type == "hotspot_boundary":
                ext_addr = get_hbm_base_addr(NUM_Y//2)
                accesses = [(ext_addr, rw, wide_length)]
            elif traffic_type == "hotspot":
                ext_addr = get_xy_base_addr(NUM_X//2, NUM_Y//2)
                accesses = [(ext_addr, rw, wide_length)]
            elif traffic_type == "matmul":
                # access matrix A from HBM
                accesses = [(get_hbm_base_addr(y), "read", wide_length//2)]
                # access matrix B from HBM
                for i in range(NUM_Y):
                    hbm_addr = get_hbm_base_addr((y + i) % NUM_Y)
                    accesses += [(hbm_addr, "read", (wide_length//2)//NUM_Y)]
                # Writeback of matrix C to HBM
                accesses += [(get_hbm_base_addr(y), "write", wide_length//4)]
            else:
                raise ValueError(f"Unknown traffic type: {traffic_type}")
            for _ in range(num_wide_bursts):
                for access in accesses:
                    src_addr = access[0] if access[1] == "read" else local_addr
                    dst_addr = local_addr if access[1] == "read" else access[0]
                    wide_jobs += gen_job_str(access[2], src_addr, dst_addr)
            for _ in range(num_narrow_bursts):
                for access in accesses:
                    src_addr = access[0] if access[1] == "read" else local_addr
                    dst_addr = local_addr if access[1] == "read" else access[0]
                    narrow_jobs += gen_job_str(access[2], src_addr, dst_addr)
            emit_jobs(wide_jobs, out_dir, "mesh", x * NUM_Y + y)
            emit_jobs(narrow_jobs, out_dir, "mesh", x * NUM_Y + y + 100)


def main():
    """Main function."""
    # parse arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("--out_dir", type=str, default="test/jobs")
    parser.add_argument("--num_narrow_bursts", type=int, default=10)
    parser.add_argument("--num_wide_bursts", type=int, default=100)
    parser.add_argument("--narrow_burst_length", type=int, default=1)
    parser.add_argument("--wide_burst_length", type=int, default=16)
    parser.add_argument("--bidir", action="store_true")
    parser.add_argument("--tb", type=str, default="dma_mesh")
    parser.add_argument("--traffic_type", type=str, default="random")
    parser.add_argument("--rw", type=str, default="read")
    args = parser.parse_args()

    kwargs = vars(args)

    if args.tb == "chimney2chimney":
        gen_chimney2chimney_traffic(**kwargs)
    elif args.tb == "nw_chimney2chimney":
        gen_nw_chimney2chimney_traffic(**kwargs)
    elif args.tb == "dma_mesh":
        gen_mesh_traffic(**kwargs)
    else:
        raise ValueError(f"Unknown testbench: {args.tb}")


if __name__ == "__main__":
    main()
