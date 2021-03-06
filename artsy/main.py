from collections import OrderedDict
import datetime as dt
from functools import partial
import logging
from pathlib import Path
import os


from bokeh import events
from bokeh.colors import RGB
from bokeh.layouts import layout
from bokeh.models import (
    Range1d, LinearColorMapper, ColorBar, FixedTicker,
    ColumnDataSource, WMTSTileSource)
from bokeh.models.widgets import Select, Div
from bokeh.plotting import figure, curdoc
from matplotlib.colors import Normalize
from matplotlib.ticker import MaxNLocator
from matplotlib.cm import ScalarMappable, get_cmap
import numpy as np
from tornado import gen


MIN_VAL = 0
MAX_VAL = 3
GREY_THRESHOLD = 0.01
ALPHA = 0.7
DATA_DIRECTORY = os.getenv('MRMS_DATADIR', '~/.mrms')


def load_data(date='latest'):
    strformat = '%Y/%m/%d/%HZ.npz'
    dir = os.path.expanduser(DATA_DIRECTORY)
    if date == 'latest':
        p = Path(dir)
        path = sorted([pp for pp in p.rglob('*.npz')], reverse=True)[0]
    else:
        path = os.path.join(dir, date.strftime(strformat))

    valid_date = dt.datetime.strptime(str(path), '{}/{}'.format(dir, strformat))
    data_load = np.load(path)
    regridded_data = data_load['data'] / 25.4  # mm to in
    X = data_load['X']
    Y = data_load['Y']
    masked_regrid = np.ma.masked_less(regridded_data, MIN_VAL)
    return masked_regrid, X, Y, valid_date


def find_all_times():
    p = Path(DATA_DIRECTORY).expanduser()
    out = OrderedDict()
    for pp in sorted(p.rglob('*.npz')):
        try:
            datetime = dt.datetime.strptime(''.join(pp.parts[-4:]),
                                            '%Y%m%d%HZ.npz')
        except ValueError:
            logging.debug('%s does not conform to expected format', pp)
            continue
        date = datetime.strftime('%Y-%m-%d %HZ')
        out[date] = datetime
    return out


# setup the coloring
levels = MaxNLocator(nbins=10*MAX_VAL + 1).tick_values(0, MAX_VAL)
cmap = get_cmap('viridis')
cmap.set_bad(color='k')
cmap.set_under(color='k')
cmap.set_over(color='w')
norm = Normalize(vmin=0, vmax=MAX_VAL, clip=False)
sm = ScalarMappable(norm=norm, cmap=cmap)
color_pal = [RGB(*val).to_hex() for val in
             sm.to_rgba(levels, bytes=True, norm=True)[:-1]]
color_mapper = LinearColorMapper(color_pal, low=sm.get_clim()[0],
                                 high=sm.get_clim()[1])
l = levels.copy()[::3]
l[0] = GREY_THRESHOLD
ticker = FixedTicker(ticks=l)
cb = ColorBar(color_mapper=color_mapper, location=(0, 0),
              scale_alpha=ALPHA, ticker=ticker)

# make the bokeh figures without the data yet
width = 1024
height = int(0.73 * width)
sfmt = '%Y-%m-%d %HZ'
tools = 'pan, box_zoom, reset, save'
map_fig = figure(plot_width=width, plot_height=height,
                 y_axis_type=None, x_axis_type='mercator',
                 toolbar_location='left', tools=tools + ', wheel_zoom',
                 active_scroll='wheel_zoom',
                 title='MRMS Precipitation (inches)')

map_fig.xaxis.axis_label = (
    'Data from http://mrms.ncep.noaa.gov/data. Map tiles from Stamen Design.\n'
    'Plot generated with Bokeh by A. Lorenzo, W. Holmgren, M. Leuthold, UA HAS'
)
map_fig.xaxis.axis_label_text_font_size = '8pt'
map_fig.xaxis.axis_line_alpha = 0

rgba_img_source = ColumnDataSource(data={'image': [], 'x': [], 'y': [],
                                         'dw': [], 'dh': []})
rgba_img = map_fig.image_rgba(image='image', x='x', y='y', dw='dw', dh='dh',
                              source=rgba_img_source)


STAMEN_TONER = WMTSTileSource(
    url=(os.getenv('TILE_SOURCE',
                   'https://stamen-tiles.a.ssl.fastly.net/toner-lite') +
         '/{Z}/{X}/{Y}.png'),
    attribution=(
        'Map tiles by <a href="http://stamen.com">Stamen Design</a>, '
        'under <a href="http://creativecommons.org/licenses/by/3.0">CC BY 3.0</a>. '
        'Map data by <a href="http://openstreetmap.org">OpenStreetMap</a>, '
        'under <a href="http://www.openstreetmap.org/copyright">ODbL</a>'
    )
)

map_fig.add_tile(STAMEN_TONER)
map_fig.add_layout(cb, 'right')

hist_height = 200
hist_width = 400
# Make the histogram figure
hist_fig = figure(plot_width=hist_width, plot_height=hist_height,
                  toolbar_location='right',
                  x_axis_label='Precipitation (inches)',
                  y_axis_label='Counts', tools=tools + ', ywheel_zoom',
                  active_scroll='ywheel_zoom',
                  x_range=Range1d(start=-.01, end=MAX_VAL))

# make histograms
bin_width = [levels[1] - levels[0]] * len(levels)
zero_width = 0.02
bin_width.insert(0, zero_width)
bin_centers = levels[:-1] + bin_width[-1] / 2
bin_centers = np.insert(bin_centers, 0, 0)
bin_centers[1] = bin_centers[1] + zero_width / 4
bin_width[1] = bin_width[1] - zero_width / 2
cpal = color_pal.copy()
cpal.insert(0, '#000000')
hist_sources = [ColumnDataSource(data={'x': [bin_centers[i]],
                                       'top': [3.0e6],
                                       'color': [cpal[i]],
                                       'bottom': [0],
                                       'width': [bin_width[i]]})
                for i in range(len(bin_centers))]
for source in hist_sources:
    hist_fig.vbar(x='x', top='top', width='width', bottom='bottom',
                  color='color', fill_alpha=ALPHA, source=source)

# line and point on map showing tapped location value
line_source = ColumnDataSource(data={'x': [-1, -1], 'y': [0, 1]})
hist_fig.line(x='x', y='y', color='red', source=line_source, alpha=ALPHA)
hover_pt = ColumnDataSource(data={'x': [0], 'y': [0], 'x_idx': [0],
                                  'y_idx': [0]})
map_fig.x(x='x', y='y', size=10, color='red', alpha=ALPHA,
          source=hover_pt, level='overlay')

widget_width = 300
file_dict = find_all_times()
dates = list(file_dict.keys())[::-1]
select_day = Select(title='Valid End', value=dates[0], options=dates,
                    width=widget_width)
info_data = ColumnDataSource(data={'current_val': [0], 'mean': [0]})
info_text = """
<div class="well">
<b>Selected Value:</b> {current_val:0.3f} <b>Mean:</b> {mean:0.3f}
</div>
"""
info_div = Div(width=widget_width)

# Setup the updates for all the data
local_data_source = ColumnDataSource(data={'masked_regrid': [0], 'xn': [0],
                                           'yn': [0],
                                           'valid_date': [dt.datetime.now()]})


def update_histogram(attr, old, new):
    # makes it so only one callback added per 100 ms
    try:
        doc.add_timeout_callback(_update_histogram, 100)
    except ValueError:
        pass


@gen.coroutine
def _update_histogram():
    left = map_fig.x_range.start
    right = map_fig.x_range.end
    bottom = map_fig.y_range.start
    top = map_fig.y_range.end

    masked_regrid = local_data_source.data['masked_regrid'][0]
    xn = local_data_source.data['xn'][0]
    yn = local_data_source.data['yn'][0]

    left_idx = np.abs(xn - left).argmin()
    right_idx = np.abs(xn - right).argmin() + 1
    bottom_idx = np.abs(yn - bottom).argmin()
    top_idx = np.abs(yn - top).argmin() + 1
    logging.debug('Updating histogram...')
    new_subset = masked_regrid[bottom_idx:top_idx, left_idx:right_idx]
    lev = np.insert(levels, 1, GREY_THRESHOLD)
    counts, _ = np.histogram(
        new_subset.clip(max=MAX_VAL), bins=lev,
        range=(levels.min(), levels.max()))
    line_source.data.update({'y': [0, counts.max()]})
    for i, source in enumerate(hist_sources):
        source.data.update({'top': [counts[i]]})
    logging.debug('Done updating histogram')

    info_data.data.update({'mean': [float(new_subset.mean())]})
    doc.add_next_tick_callback(_update_div_text)


def update_map(attr, old, new):
    try:
        doc.add_timeout_callback(_update_histogram, 100)
    except ValueError:
        pass


@gen.coroutine
def _update_map(update_range=False):
    logging.debug('Updating map...')
    valid_date = local_data_source.data['valid_date'][0]
    title = 'MRMS Precipitation (inches) {} through {}'.format(
        (valid_date - dt.timedelta(hours=24)).strftime(sfmt),
        valid_date.strftime(sfmt))
    map_fig.title.text = title
    masked_regrid = local_data_source.data['masked_regrid'][0].copy()
    masked_regrid = np.ma.masked_where(masked_regrid < GREY_THRESHOLD,
                                       masked_regrid)
    xn = local_data_source.data['xn'][0]
    yn = local_data_source.data['yn'][0]
    rgba_vals = sm.to_rgba(masked_regrid, bytes=True, alpha=ALPHA)
    dx = xn[1] - xn[0]
    dy = yn[1] - yn[0]
    rgba_img_source.data.update({'image': [rgba_vals],
                                 'x': [xn[0] - dx / 2],
                                 'y': [yn[0] - dy / 2],
                                 'dw': [xn[-1] - xn[0] + dx],
                                 'dh': [yn[-1] - yn[0] + dy]})
    if update_range:
        map_fig.x_range.start = xn[0]
        map_fig.x_range.end = xn[-1]
        map_fig.y_range.start = yn[0]
        map_fig.y_range.end = yn[-1]
    logging.debug('Done updating map')


def update_data(attr, old, new):
    try:
        doc.add_timeout_callback(_update_data, 100)
    except ValueError:
        pass


@gen.coroutine
def _update_data(update_range=False):
    logging.debug('Updating data...')
    date = file_dict[select_day.value]
    masked_regrid, X, Y, valid_date = load_data(date)
    xn = X[0]
    yn = Y[:, 0]
    local_data_source.data.update({'masked_regrid': [masked_regrid],
                                   'xn': [xn], 'yn': [yn],
                                   'valid_date': [valid_date]})
    curdoc().add_next_tick_callback(partial(_update_map, update_range))
    curdoc().add_timeout_callback(_update_histogram, 10)
    curdoc().add_next_tick_callback(_move_hist_line)
    logging.debug('Done updating data')


def move_click_marker(event):
    try:
        doc.add_timeout_callback(partial(_move_click_marker, event), 50)
    except ValueError:
        pass


@gen.coroutine
def _move_click_marker(event):
    x = event.x
    y = event.y

    xn = local_data_source.data['xn'][0]
    yn = local_data_source.data['yn'][0]

    x_idx = np.abs(xn - x).argmin()
    y_idx = np.abs(yn - y).argmin()

    hover_pt.data.update({'x': [xn[x_idx]], 'y': [yn[y_idx]],
                          'x_idx': [x_idx], 'y_idx': [y_idx]})
    curdoc().add_next_tick_callback(_move_hist_line)


@gen.coroutine
def _move_hist_line():
    x_idx = hover_pt.data['x_idx'][0]
    y_idx = hover_pt.data['y_idx'][0]
    masked_regrid = local_data_source.data['masked_regrid'][0]
    val = masked_regrid[y_idx, x_idx]
    info_data.data.update({'current_val': [float(val)]})
    doc.add_next_tick_callback(_update_div_text)

    if val <= MIN_VAL or val == np.nan:
        val = MIN_VAL * 1.05
    elif val > MAX_VAL:
        val = MAX_VAL * .99
    line_source.data.update({'x': [val, val]})



@gen.coroutine
def _update_div_text():
    current_val = info_data.data['current_val'][0]
    mean = info_data.data['mean'][0]
    info_div.text = info_text.format(current_val=current_val,
                                     mean=mean)

# python callbacks
map_fig.x_range.on_change('start', update_histogram)
map_fig.x_range.on_change('end', update_histogram)
map_fig.y_range.on_change('start', update_histogram)
map_fig.y_range.on_change('end', update_histogram)
map_fig.on_event(events.Tap, move_click_marker)

select_day.on_change('value', update_data)

# layout the document
lay = layout([
    [map_fig],
    [[select_day, info_div],
     hist_fig]], sizing_mode='scale_width')

doc = curdoc()
doc.title = 'UA HAS ARTSy'
doc.template_variables.update(max_val=MAX_VAL, min_val=GREY_THRESHOLD)
doc.add_root(lay)
doc.add_next_tick_callback(partial(_update_data, True))
