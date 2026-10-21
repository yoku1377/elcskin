"""The H2.2 tactile point-cloud surface, driven by H3's eight Fz allocations.

Geometry, interpolation, HSL ramp, and lift use the values from the older
TfgMonitor/src/components/TactilePointCloud.js. The dots are display samples,
not additional measured cells or physical displacement.
"""

import numpy as np
import pyqtgraph.opengl as gl


CHANNEL_CENTRES = np.asarray(
    [(x, y) for y in (1.2, 0.4, -0.4, -1.2) for x in (-0.42, 0.42)],
    dtype=np.float32,
)


def _hsl_rgb(hue, saturation, lightness):
    """Vectorized equivalent of THREE.Color.setHSL for the H2.2 color ramp."""
    chroma = (1.0 - np.abs(2.0 * lightness - 1.0)) * saturation
    sector = hue * 6.0
    secondary = chroma * (1.0 - np.abs(np.mod(sector, 2.0) - 1.0))
    zero = np.zeros_like(hue)
    red = np.select(
        [sector < 1, sector < 2, sector < 3, sector < 4, sector < 5],
        [chroma, secondary, zero, zero, secondary], default=chroma,
    )
    green = np.select(
        [sector < 1, sector < 2, sector < 3, sector < 4, sector < 5],
        [secondary, chroma, chroma, secondary, zero], default=zero,
    )
    blue = np.select(
        [sector < 1, sector < 2, sector < 3, sector < 4, sector < 5],
        [zero, zero, secondary, chroma, chroma], default=secondary,
    )
    offset = lightness - chroma / 2.0
    return red + offset, green + offset, blue + offset


class TactilePointCloud:
    def __init__(self):
        columns, rows = 34, 72
        u = np.tile(np.linspace(-1.0, 1.0, columns, dtype=np.float32), rows)
        v = np.repeat(np.linspace(-1.0, 1.0, rows, dtype=np.float32), columns)
        cap = np.sqrt(np.maximum(
            0.22, 1.0 - np.maximum(0.0, np.abs(v) - 0.48) ** 2 / 0.52 ** 2,
        ))
        x = u * (0.78 * cap + 0.08)
        y = -v * 1.6
        self.base_z = (0.12 + 0.16 * (1.0 - u * u) +
                       0.045 * (1.0 - v * v)).astype(np.float32)
        # Rotate H2.2's Y-up scene into Qt's Z-up world: (x,y,z)->(x,-z,y).
        self.positions = np.column_stack((x, -self.base_z, y)).astype(np.float32)

        samples = np.column_stack((x, y))
        distance_sq = ((samples[:, None, :] -
                        CHANNEL_CENTRES[None, :, :]) ** 2).sum(axis=2)
        weights = np.exp(-distance_sq / 0.24).astype(np.float32)
        self.weights = weights / weights.sum(axis=1, keepdims=True)
        self.colors = np.empty((len(x), 4), dtype=np.float32)
        self._paint(np.zeros(8, dtype=np.float32))
        self.item = gl.GLScatterPlotItem(
            pos=self.positions, color=self.colors, size=3.35,
            pxMode=True, glOptions="translucent",
        )

    def _paint(self, loads):
        interpolated = self.weights @ np.maximum(loads, 0.0)
        ratio = np.clip(interpolated / 8.0, 0.0, 1.0)
        self.positions[:, 1] = -self.base_z - np.power(ratio, 0.7) * 0.36
        hue = 0.52 - ratio * 0.51
        lightness = 0.44 + ratio * 0.14
        self.colors[:, 0], self.colors[:, 1], self.colors[:, 2] = _hsl_rgb(
            hue, 0.93, lightness,
        )
        self.colors[:, 3] = 0.96

    def set_loads(self, loads):
        self._paint(np.asarray(loads, dtype=np.float32).reshape(8))
        self.item.setData(pos=self.positions, color=self.colors)
