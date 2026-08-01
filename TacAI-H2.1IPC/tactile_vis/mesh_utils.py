"""
3D mesh generation utilities

Provides cylinder and cone mesh builders used for constructing
3D force arrow models.

Author: Yijie Luo
Email: yijie_luo@tongji.edu.cn
"""

import numpy as np
import pyqtgraph.opengl as gl


def create_cylinder_mesh(radius, height, rows=10, cols=20):
    """Generate a cylinder mesh

    Args:
        radius: Cylinder radius
        height: Cylinder height
        rows: Number of segments along the height axis
        cols: Number of segments around the circumference

    Returns:
        gl.MeshData: Mesh data for use with GLMeshItem
    """
    vertices = []
    faces = []

    for i in range(rows):
        z_val = height * i / (rows - 1)
        for j in range(cols):
            angle = 2 * np.pi * j / cols
            x = radius * np.cos(angle)
            y = radius * np.sin(angle)
            vertices.append([x, y, z_val])

    for i in range(rows - 1):
        for j in range(cols):
            j_next = (j + 1) % cols
            idx1 = i * cols + j
            idx2 = (i + 1) * cols + j
            idx3 = (i + 1) * cols + j_next
            idx4 = i * cols + j_next
            faces.append([idx1, idx2, idx3])
            faces.append([idx1, idx3, idx4])

    vertices = np.array(vertices, dtype=np.float32)
    faces = np.array(faces, dtype=np.uint32)
    return gl.MeshData(vertexes=vertices, faces=faces)


def create_cone_mesh(radius, height, rows=10, cols=20):
    """Generate a cone mesh

    Args:
        radius: Cone base radius
        height: Cone height
        rows: Number of segments along the height axis
        cols: Number of segments around the circumference

    Returns:
        gl.MeshData: Mesh data for use with GLMeshItem
    """
    vertices = []
    faces = []

    vertices.append([0.0, 0.0, height])

    for i in range(rows):
        z_val = height * i / (rows - 1)
        r = radius * (1 - z_val / height)
        for j in range(cols):
            angle = 2 * np.pi * j / cols
            x = r * np.cos(angle)
            y = r * np.sin(angle)
            vertices.append([x, y, z_val])

    for i in range(rows - 1):
        for j in range(cols):
            j_next = (j + 1) % cols
            idx1 = 1 + i * cols + j
            idx2 = 1 + (i + 1) * cols + j
            idx3 = 1 + (i + 1) * cols + j_next
            idx4 = 1 + i * cols + j_next

            if i == rows - 2:
                faces.append([0, idx4, idx1])

            faces.append([idx1, idx2, idx3])
            faces.append([idx1, idx3, idx4])

    vertices = np.array(vertices, dtype=np.float32)
    faces = np.array(faces, dtype=np.uint32)
    return gl.MeshData(vertexes=vertices, faces=faces)