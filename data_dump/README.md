BlockFS data dump
==

This directory contains data stored in BlockFS storage combined with parts of
the ERT config file. The data is stored as netCDF4.

# Layout

The root group of the file contains only subgroups that represent each
realisations. These are `REAL_{index}` where `{index}` is the realisation
number. (`REAL_0`, `REAL_1` and so on). There may be gaps in these but in the
test data there aren't any.

The ERT data types are found as subgroups of the aforementioned groups. Eg, all
`GEN_DATA` is located under `/REAL_{index}/GEN_DATA`. Each ERT type is
inconveniently formatted differently from other types. This document describes
where the data is located.

## `GEN_KW`

`GEN_KW` are parameters that are sampled by ERT. These are defined in a
user-specified file. Internally within ERT, these parameters are sampled from
the standard normal distribution, but the user can specify other distributions
which ERT will convert its sampled values to.

In the data file, each separate `GEN_KW` entry in the ERT config is a group.
(eg. `/REAL_0/GEN_KW/SNAKE_OIL_PARAM` for `SNAKE_OIL_PARAM` `GEN_KW` for
realisation 0.) The dataset contains one dimension `name` for entries within a
`GEN_KW` definition file, as well as the variables:

* `standard_normal`: What ERT samples and is also the only thing that is stored
  on-disk in the BlockFS system.
* `transformed`: Values as transformed by ERT. This is what the user sees in
  `parameters.txt`.
* `probability_distribution`: The ERT name for the probability distribution used to convert `standard_normal` to `transformed`.
* `probability_distribution_parameters`: JSON-ified dictionary of probability distribution parameters.

## `GEN_DATA`

All `GEN_DATA`s are in the same group under `/REAL_{index}/GEN_DATA`. This
dataset contains the dimensions `report_step` as dates and `index` as the
`GEN_DATA` indexing. The dates have been obtained by indexing the `time-map`
vector at the given `REPORT_STEP` indices. Each NetCDF variable is a separate
`GEN_DATA` entry.

## `SUMMARY`

All `SUMMARY`s are in the same group under `/REAL_{index}/SUMMARY`. This dataset
contains the dimension `report_step`, which is the `time-map`. Each NetCDF
variable is a separate `SUMMARY` entry.

## `FIELD`

Each ERT `FIELD` is a subgroup of `/REAL_{index}/FIELD`, where the name of the
group is the name of the field. The dimensions are `x`, `y` and `z`, which
correspond to the 3-D axes of fields. The data for the field is given by the
`VALUES` variable.

## `SURFACE`

Each ERT `SURFACE` is a subgroup of `/REAL_{index}/SURFACE`, where the name of
the group is the name of the surface. The data for the surface is given by the
`VALUES` variable.

# Reading

To programmatically read data from the files it is recommended to use both the
`netCDF4` package for low-level access, and `xarray` package for manipulating or
converting the data.

The `xarray` package does not make it easy to navigate groups which is why
`netCDF4` is used. `xarray` uses `netCDF4` as a backend to open NetCDF version 4
files, so both packages need to be installed anyway.

## `netCDF4` package

Groups can be navigated using `netCDF4`. First, open a file:

``` python
import netCDF4

ds = netCDF4.Dataset("some-dataset.nc")
```

Groups are given using the `.groups` property. This returns a Python `dict` with
the keys being the name of the group, and the value being a `netCDF4.Group`
object. Groups may have subgroups.

``` python
for name, group in ds.groups.items():
    for subname, subgroup in group.group.items():
        print(f"{name}/{subname} = {subgroup}")
```

Specific subgroups can be accessed by `ds.groups["some_group"]` or
`ds.group["some_group"]`. It is also possible to access subgroups by path, as if
in a file structure: `ds["/foo/bar/baz"]`, which is equivalent to
`ds.groups["foo"].groups["bar"].groups["baz"]`.

NetCDF datasets have _variables_, which store actual data, and _dimensions_ (aka
_coordinates_), which are the axes for the data. Within a group, if two
different variables refer to a dimension by one name, then both variables will
have the same dimension values. For example, the variables for porosity and
pressure are different, but may share the same `x`, `y`, `z` axes.

To access dimensions, use the `.dimensions` property. To access variables, use
the `.variables` property. Both return Python `dict`s with keys as the dimension
or variable names and the values are `netCDF4` data class instances. Note that
dimensions are also variables. Unlike `xarray`, the low-level `netCDF4` package
has no concept of "data variable". To get the data variables, do:

``` python
data_vars = {
  key: val
  for key, val in ds.variables.items()
  if key not in ds.dimensions
}
```

`netCDF4` variables are iterable their data can be obtained by calling
`list(ds["some_variable"])`. The package also provides the numpy-specific
`__array__`, allowing for an efficient `numpy.array(ds["some_variable"])`.

## `xarray` package

`xarray` by itself does not allow us to navigate the group structure of the dataset. When loading a specific group from file, it is possible to use the `group=` kwarg:

``` python
import xarray as xr

ds = xr.open_dataset("some-dataset.nc", group="some_group")
```

By default the root group (`/`) is opened. Because `xarray` uses `netCDF4` as
backend, it is possible to use the `xarray` functionality on an already-opened
`netCDF4.Dataset` without reopening and rereading the file using `xr.backends.NetCDF4DataStore`:

``` python
import xarray as xr
import netCDF4

netcdf4_dataset = netCDF4.Dataset("some-dataset.nc")
for group in netcdf4_dataset.groups.values():
    ds = xr.open_dataset(xr.backends.NetCDF4DataStore(group))
    print(ds)
```

While `xarray` is quite powerful and stores data well, it isn't that pretty when
it comes to actually seeing the data. As such, it is often useful to call
`.to_dataframe()`, which converts the dataset to the tabular `pandas.DataFrame`.
This potentially has performance issues, as well as data duplication concerns
when dealing with high-dimensional data.
