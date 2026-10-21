"""Iterative RDP in meters, retaining both route endpoints."""
from typing import Dict, List
import numpy as np

class PathSimplifier:
    @staticmethod
    def _distances(points, start, end):
        scale = np.array([111139.0, 111139.0 * np.cos(np.radians((start[0] + end[0]) / 2))])
        offset = (points - start) * scale
        line = (end - start) * scale
        squared = float(np.dot(line, line))
        if squared < 1e-12:
            return np.linalg.norm(offset, axis=-1)
        fraction = np.clip(offset @ line / squared, 0, 1)
        return np.linalg.norm(offset - np.asarray(fraction)[..., None] * line, axis=-1)

    @staticmethod
    def _perpendicular_distance(point, line_start, line_end):
        return float(PathSimplifier._distances(np.asarray(point), np.asarray(line_start), np.asarray(line_end)))

    def simplify_rdp(self, points, epsilon_meters=8.0):
        points = np.asarray(points, dtype=float)
        if not np.isfinite(epsilon_meters) or epsilon_meters < 0:
            raise ValueError("epsilon_meters must be nonnegative")
        if len(points) < 3:
            return points
        if points.shape[1:] != (2,) or not np.isfinite(points).all():
            raise ValueError("Expected finite latitude/longitude pairs")
        keep = {0, len(points) - 1}
        pending = [(0, len(points) - 1)]
        while pending:
            start, end = pending.pop()
            if end - start < 2:
                continue
            distances = self._distances(points[start+1:end], points[start], points[end])
            index = int(np.argmax(distances)) + start + 1
            if distances[index-start-1] > epsilon_meters:
                keep.add(index)
                pending.extend(((start, index), (index, end)))
        return points[sorted(keep)]

    def downsample_gps_stream(self, coordinates: List[Dict[str, float]], max_points_for_api=50):
        if max_points_for_api < 2:
            raise ValueError("At least two route endpoints must be retained")
        if len(coordinates) <= max_points_for_api:
            return coordinates
        raw = np.array([[c["lat"], c["lon"]] for c in coordinates], dtype=float)
        simplified = self.simplify_rdp(raw, epsilon_meters=10.0)
        if len(simplified) > max_points_for_api:
            simplified = simplified[np.linspace(0, len(simplified)-1, max_points_for_api, dtype=int)]
        return [{"lat": float(p[0]), "lon": float(p[1])} for p in simplified]
