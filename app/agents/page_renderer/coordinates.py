from typing import List, Tuple
import pymupdf


class CoordinateTransformer:
    """Manages coordinate transformations between pixel space, visual PDF space, and unrotated PDF space."""

    def __init__(self, page: pymupdf.Page, image_width_px: int, image_height_px: int):
        self.page = page
        self.image_width_px = image_width_px
        self.image_height_px = image_height_px

        rect = page.rect
        cbox = page.cropbox
        self.visual_width_pt = rect.width
        self.visual_height_pt = rect.height

        # Calculate exact scale factors
        self.scale_x = image_width_px / self.visual_width_pt if self.visual_width_pt > 0 else 1.0
        self.scale_y = image_height_px / self.visual_height_pt if self.visual_height_pt > 0 else 1.0

        # Pixel to visual transformation (accounting for visual viewport origin rect.x0, rect.y0)
        # P_pixel = (P_vis - rect.origin) * Matrix(scale_x, scale_y)
        self.m_vis_to_px = pymupdf.Matrix(1, 0, 0, 1, -rect.x0, -rect.y0) * pymupdf.Matrix(self.scale_x, self.scale_y)
        self.m_px_to_vis = ~self.m_vis_to_px

        # Visual to unrotated user space transformation (accounting for cropbox translation)
        # P_raw = (P_vis * derotation_matrix) + cropbox.origin
        m_crop_trans = pymupdf.Matrix(1, 0, 0, 1, cbox.x0, cbox.y0)
        self.m_vis_to_unrot = page.derotation_matrix * m_crop_trans
        self.m_unrot_to_vis = ~m_crop_trans * page.rotation_matrix

        # Composed end-to-end matrices between pixel space and raw unrotated PDF space
        self.m_px_to_unrot = self.m_px_to_vis * self.m_vis_to_unrot
        self.m_unrot_to_px = self.m_unrot_to_vis * self.m_vis_to_px

    @staticmethod
    def matrix_to_list(mat: pymupdf.Matrix) -> List[float]:
        """Converts PyMuPDF Matrix(a, b, c, d, e, f) to a 6-element list rounded for JSON serialization."""
        return [
            round(float(mat.a), 8),
            round(float(mat.b), 8),
            round(float(mat.c), 8),
            round(float(mat.d), 8),
            round(float(mat.e), 8),
            round(float(mat.f), 8),
        ]

    def pixel_to_visual(self, x_p: float, y_p: float) -> Tuple[float, float]:
        """Transforms a point from image pixels to visual PDF page points."""
        pt = pymupdf.Point(x_p, y_p) * self.m_px_to_vis
        return round(float(pt.x), 4), round(float(pt.y), 4)

    def visual_to_pixel(self, x_v: float, y_v: float) -> Tuple[float, float]:
        """Transforms a point from visual PDF page points to image pixels."""
        pt = pymupdf.Point(x_v, y_v) * self.m_vis_to_px
        return round(float(pt.x), 2), round(float(pt.y), 2)

    def pixel_to_unrotated(self, x_p: float, y_p: float) -> Tuple[float, float]:
        """Transforms a point from image pixels to raw unrotated PDF user units."""
        pt = pymupdf.Point(x_p, y_p) * self.m_px_to_unrot
        return round(float(pt.x), 4), round(float(pt.y), 4)

    def unrotated_to_pixel(self, x_u: float, y_u: float) -> Tuple[float, float]:
        """Transforms a point from raw unrotated PDF user units to image pixels."""
        pt = pymupdf.Point(x_u, y_u) * self.m_unrot_to_px
        return round(float(pt.x), 2), round(float(pt.y), 2)

    def get_matrices_dict(self) -> dict:
        """Returns all 4 matrices serialized as lists."""
        return {
            "matrix_pixel_to_visual": self.matrix_to_list(self.m_px_to_vis),
            "matrix_pixel_to_unrotated": self.matrix_to_list(self.m_px_to_unrot),
            "matrix_visual_to_pixel": self.matrix_to_list(self.m_vis_to_px),
            "matrix_unrotated_to_pixel": self.matrix_to_list(self.m_unrot_to_px),
        }
