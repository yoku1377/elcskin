"""
Global constants and color configuration

Author: Yijie Luo
Email: yijie_luo@tongji.edu.cn
"""

ROWS = 4
COLS = 3
FLOATS_PER_FRAME = ROWS * COLS * 3

COLORS = {
    'bg': '#121212',
    'panel_bg': '#1e1e1e',
    'text': '#e0e0e0',
    'text_secondary': '#888888',
    'accent': '#00bcd4',
    'success': '#4caf50',
    'warning': '#ff9800',
    'danger': '#f44336',
    'grid': '#2a2a2a',
}


def get_color_for_force(magnitude, max_magnitude=5.0):
    """Map force magnitude to color gradient: blue -> cyan -> green -> yellow -> red"""
    import numpy as np
    t = min(magnitude / max_magnitude, 1.0)

    if t < 0.2:
        c = np.array([0.0, 0.4 + t, 1.0, 1.0])
    elif t < 0.4:
        t2 = (t - 0.2) / 0.2
        c = np.array([0.0, 0.8 + t2 * 0.2, 1.0 - t2 * 0.5, 1.0])
    elif t < 0.6:
        t2 = (t - 0.4) / 0.2
        c = np.array([t2, 1.0, 0.5 - t2 * 0.5, 1.0])
    elif t < 0.8:
        t2 = (t - 0.6) / 0.2
        c = np.array([1.0, 1.0 - t2 * 0.5, 0.0, 1.0])
    else:
        t2 = (t - 0.8) / 0.2
        c = np.array([1.0, 0.5 - t2 * 0.5, 0.0, 1.0])
    return c