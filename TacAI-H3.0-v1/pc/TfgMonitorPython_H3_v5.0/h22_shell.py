"""H2.2's MC-Tac reference shell and faint halo for the Qt OpenGL view."""

from pathlib import Path
import struct
import sys

import numpy as np
import pyqtgraph.opengl as gl


def _asset_path():
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return root / "assets" / "mctac-holderSlimW.stl"


def shell_item():
    data = _asset_path().read_bytes()
    count = struct.unpack_from("<I", data, 80)[0]
    if len(data) != 84 + count * 50:
        raise ValueError("Invalid MC-Tac STL asset")
    triangles = np.frombuffer(
        data, dtype=np.dtype([
            ("normal", "<f4", (3,)),
            ("vertices", "<f4", (3, 3)),
            ("attribute", "<u2"),
        ]), offset=84, count=count,
    )["vertices"].reshape(-1, 3)
    centre = (triangles.min(axis=0) + triangles.max(axis=0)) / 2.0
    x, y, z = (triangles - centre).T
    # H2.2: rotation.set(pi/2, 0, pi/2), scale=.041,
    # position=(0,-.08,-.72), then rotate the H2.2 Y-up scene into Qt Z-up.
    vertices = np.column_stack((-y * 0.041, -x * 0.041 + 0.72,
                                -z * 0.041 - 0.08)).astype(np.float32)
    faces = np.arange(len(vertices), dtype=np.uint32).reshape(-1, 3)
    return gl.GLMeshItem(
        meshdata=gl.MeshData(vertexes=vertices, faces=faces),
        drawFaces=False, drawEdges=True,
        edgeColor=(0.06, 0.17, 0.21, 0.14),
        smooth=False, glOptions="translucent",
    )


def halo_item():
    vertices = np.asarray([
        [-1.25, 0.12, -2.15], [1.25, 0.12, -2.15],
        [1.25, 0.12, 2.15], [-1.25, 0.12, 2.15],
    ], dtype=np.float32)
    return gl.GLMeshItem(
        meshdata=gl.MeshData(vertexes=vertices,
                             faces=np.asarray([[0, 1, 2], [0, 2, 3]])),
        color=(0.043, 0.494, 0.56, 0.045),
        drawFaces=True, drawEdges=False,
        smooth=False, glOptions="translucent",
    )
