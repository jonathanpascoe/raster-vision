from typing import TYPE_CHECKING
import logging

from rasterio.features import rasterize
import numpy as np
from shapely.geometry import shape
from shapely.strtree import STRtree
from shapely.ops import transform

from rastervision.core.data import (ActivateMixin, ActivationError)
from rastervision.core.data.raster_source import RasterSource

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from rastervision.core.box import Box
    from rastervision.core.data import (VectorSource, RasterizerConfig,
                                        CRSTransformer)


def geoms_to_raster(str_tree, rasterizer_config, window, extent):
    background_class_id = rasterizer_config.background_class_id
    all_touched = rasterizer_config.all_touched

    log.debug('Cropping shapes to window...')
    # Crop shapes against window, remove empty shapes, and put in window frame of
    # reference.
    window_geom = window.to_shapely()
    shapes = str_tree.query(window_geom)
    shapes = [(s, s.class_id) for s in shapes]
    shapes = [(s.intersection(window_geom), c) for s, c in shapes]
    shapes = [(s, c) for s, c in shapes if not s.is_empty]

    def to_window_frame(x, y, z=None):
        return (x - window.xmin, y - window.ymin)

    shapes = [(transform(to_window_frame, s), c) for s, c in shapes]
    log.debug('# of shapes in window: {}'.format(len(shapes)))

    out_shape = (window.get_height(), window.get_width())

    # rasterize needs to be passed >= 1 shapes.
    if shapes:
        log.debug('rasterio.rasterize()...')
        raster = rasterize(
            shapes,
            out_shape=out_shape,
            fill=background_class_id,
            dtype=np.uint8,
            all_touched=all_touched)
    else:
        raster = np.full(out_shape, background_class_id, dtype=np.uint8)

    return raster


class RasterizedSource(ActivateMixin, RasterSource):
    """A RasterSource based on the rasterization of a VectorSource."""

    def __init__(self, vector_source: 'VectorSource',
                 rasterizer_config: 'RasterizerConfig', extent: 'Box',
                 crs_transformer: 'CRSTransformer'):
        """Constructor.

        Args:
            vector_source: (VectorSource)
            rasterizer_config: (RasterizerConfig)
            extent: (Box) extent of corresponding imagery RasterSource
            crs_transformer: (CRSTransformer)
        """
        self.vector_source = vector_source
        self.rasterizer_config = rasterizer_config
        self.extent = extent
        self.crs_transformer = crs_transformer
        self.activated = False

        super().__init__(channel_order=[0], num_channels=1)

    def get_extent(self):
        """Return the extent of the RasterSource.

        Returns:
            Box in pixel coordinates with extent
        """
        return self.extent

    def get_dtype(self):
        """Return the numpy.dtype of this scene"""
        return np.uint8

    def get_crs_transformer(self):
        """Return the associated CRSTransformer."""
        return self.crs_transformer

    def _get_chip(self, window):
        """Return the chip located in the window.

        Polygons falling within the window are rasterized using the class_id, and
        the background is filled with background_class_id. Also, any pixels in the
        window outside the extent are zero, which is the don't-care class for
        segmentation.

        Args:
            window: Box

        Returns:
            [height, width, channels] numpy array
        """
        if not self.activated:
            raise ActivationError('GeoJSONSource must be activated before use')

        log.debug('Rasterizing window: {}'.format(window))
        chip = geoms_to_raster(self.str_tree, self.rasterizer_config, window,
                               self.get_extent())
        # Add third singleton dim since rasters must have >=1 channel.
        return np.expand_dims(chip, 2)

    def _activate(self):
        geojson = self.vector_source.get_geojson()
        geoms = []
        for f in geojson['features']:
            geom = shape(f['geometry'])
            geom.class_id = f['properties']['class_id']
            geoms.append(geom)
        self.str_tree = STRtree(geoms)
        self.activated = True

    def _deactivate(self):
        self.str_tree = None
        self.activated = False
