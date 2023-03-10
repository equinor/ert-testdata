#!/usr/bin/env python3.10
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Tuple, Iterable

import numpy as np
import pandas as pd
import xarray as xr
import xtgeo
from ert import _clib
from ert._c_wrappers import ResPrototype
from ert._c_wrappers.enkf import EnkfConfigNode, EnKFMain, EnkfNode, ErtConfig, NodeId
from ert._c_wrappers.enkf.enums import ErtImplType
from ecl.grid import EclGrid

summary_get = ResPrototype("double summary_get(void*, int)", bind=False)


class XtgeoGridAdapter:
    def __init__(self, ecl_grid: EclGrid) -> None:
        self._grid = ecl_grid

    @property
    def ncol(self) -> int:
        return self._grid.get_nx()

    @property
    def nrow(self) -> int:
        return self._grid.get_ny()

    @property
    def nlay(self) -> int:
        return self._grid.get_nz()

    def get_actnum(self):
        return pd.DataFrame(list(self._grid.export_actnum()))

    @property
    def dimensions(self):
        return (self._grid.get_dims()[3], 1)


class Reader:
    def __init__(self, config_path: os.PathLike[str], case: str) -> None:
        self.ert_config = ErtConfig.from_file(config_path)
        self.ert = EnKFMain(self.ert_config)
        self.fs = self.ert.storage_manager[case]
        self.impl = {
            ErtImplType.EXT_PARAM: self._load_ext_param,
            ErtImplType.FIELD: self._load_field,
            ErtImplType.SURFACE: self._load_surface,
            ErtImplType.GEN_DATA: self._load_gen_data,
            ErtImplType.SUMMARY: self._load_summary,
            ErtImplType.GEN_KW: self._load_gen_kw,
        }

        tm = self.fs.getTimeMap()
        self.time_map = np.array([tm[i] for i in range(len(tm))])

    def _load_ext_param(
        self, config: EnkfConfigNode, realization_index: int
    ) -> Tuple[str, xr.Dataset]:
        raise NotImplementedError

    def _load_field(
        self, config: EnkfConfigNode, realization_index: int
    ) -> Tuple[str, xr.Dataset]:
        model = config.getFieldModelConfig()
        node = EnkfNode(config)
        node.load(self.fs, NodeId(0, realization_index))
        _clib.field.generate_parameter_file(node, "/tmp", "field.grdecl")
        gp = xtgeo.gridproperty_from_file(
            "/tmp/field.grdecl",
            fformat="grdecl",
            name=config.getKey(),
            grid=XtgeoGridAdapter(model.get_grid()),
        )
        ds = xr.Dataset({"VALUES": (("x", "y", "z"), gp.values3d)})
        return f"FIELD/{config.getKey()}", ds

    def _load_summary(
        self, config: EnkfConfigNode, realization_index: int
    ) -> Tuple[str, xr.Dataset]:
        node = EnkfNode(config)
        data = []
        for report_step in range(len(self.time_map)):
            node_id = NodeId(report_step, realization_index)
            if not node.has_data(self.fs, node_id):
                continue
            node.load(self.fs, node_id)
            data.append(summary_get(node.valuePointer(), report_step))
        if not data:
            return None
        ds = xr.Dataset(
            {config.getKey(): ("report_step", data)},
            coords={"report_step": self.time_map},
        )
        return "SUMMARY", ds

    def _load_gen_data(
        self, config: EnkfConfigNode, realization_index: int
    ) -> Tuple[str, xr.Dataset]:
        node = EnkfNode(config)
        model = config.getDataModelConfig()
        data = []
        report_steps = []
        for report_step in model.getReportSteps():
            node.load(self.fs, NodeId(report_step, realization_index))
            data.append(list(node.asGenData().getData()))
            report_steps.append(self.time_map[report_step])

        data = np.array(data)
        ds = xr.Dataset(
            {config.getKey(): (("report_step", "index"), data)},
            coords={
                "report_step": report_steps,
                "index": range(data.shape[1]),
            },
        )
        return "GEN_DATA", ds

    def _load_gen_kw(
        self, config: EnkfConfigNode, realization_index: int
    ) -> Tuple[str, xr.Dataset]:
        model = config.getKeywordModelConfig()
        node = EnkfNode(config)
        node.load(self.fs, NodeId(0, realization_index))
        node = node.asGenKw()

        data = [
            (node._iget_key(i), node._data_iget(i, False), node._data_iget(i, True))
            for i in range(len(node))
        ]
        names, raw, transformed = list(zip(*data))

        funcs = [x["function"] for x in model.get_priors()]
        params = [json.dumps(x["parameters"]) for x in model.get_priors()]

        ds = xr.Dataset(
            {
                "standard_normal": ("name", list(raw)),
                "transformed": ("name", list(transformed)),
                "probability_distribution": ("name", funcs),
                "probability_distribution_parameters": ("name", params),
            },
            coords={"name": ("name", list(names))},
        )
        return f"GEN_KW/{config.getKey()}", ds

    def _load_surface(
        self, config: EnkfConfigNode, realization_index: int
    ) -> Tuple[str, xr.Dataset]:
        node = EnkfNode(config)
        node.load(self.fs, NodeId(0, realization_index))
        _clib.surface.generate_parameter_file(node, "/tmp", "surface")
        surface = xtgeo.surface_from_file("/tmp/surface", fformat="irap_ascii")
        return f"SURFACE/{config.getKey()}", xr.Dataset(surface.dataframe())

    def load_all(self) -> Iterable[Tuple[int, str, xr.Dataset]]:
        ensemble_config = self.ert_config.ensemble_config
        keys = ensemble_config.alloc_keylist()
        for key in keys:
            for realization_index in range(self.ert.getEnsembleSize()):
                config = ensemble_config[key]
                func = self.impl.get(config.getImplementationType())
                if func is None:
                    continue
                data = func(config, realization_index)
                if data is not None:
                    group, dataset = data
                    yield realization_index, group, dataset


@contextmanager
def chdir(path: str) -> None:
    current = os.getcwd()
    os.chdir(path)
    yield
    os.chdir(current)


def dump_data(ert_config: str, case: str) -> None:
    ert_config = os.path.join(os.path.dirname(__file__), "..", ert_config)
    output_file = os.path.join(
        os.path.dirname(__file__), f"{os.path.basename(os.path.dirname(ert_config))}.nc"
    )
    with chdir(os.path.dirname(ert_config)):
        reader = Reader(ert_config, case)

        mode = "w"
        for realization_index, group, dataset in reader.load_all():
            dataset.to_netcdf(
                output_file,
                mode=mode,
                engine="netcdf4",
                group=f"REAL_{realization_index}/{group}",
            )
            mode = "a"


def main():
    dump_data("snake_oil/snake_oil.ert", "default_0")
    dump_data("simple_case/config.ert", "default")


if __name__ == "__main__":
    main()
