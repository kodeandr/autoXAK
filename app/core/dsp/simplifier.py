import numpy as np
from typing import List, Dict

class PathSimplifier:
    """
    Алгоритм Рамера — Дугласа — Пекера (RDP) для геометрического сжатия GPS-трека.
    Сокращает объем точек на 95-99%, сохраняя ключевые маневры и повороты.
    """

    @staticmethod
    def _perpendicular_distance(point: np.ndarray, line_start: np.ndarray, line_end: np.ndarray) -> float:
        """Вычисление расстояния от точки до отрезка прямой в метрах (приближение проекции)."""
        if np.allclose(line_start, line_end):
            return float(np.linalg.norm(point - line_start))

        # Перевод градусов широты/долготы в эквивалентные метры
        # 1 градус широты ~ 111 139 м, долготы в Москве ~ 62 800 м
        scale = np.array([111139.0, 62800.0])
        p = point * scale
        p1 = line_start * scale
        p2 = line_end * scale

        line_vec = p2 - p1
        point_vec = p - p1
        line_len = np.linalg.norm(line_vec)
        line_unitvec = line_vec / line_len

        proj_length = np.dot(point_vec, line_unitvec)
        proj_length = np.clip(proj_length, 0.0, line_len)
        closest_point = p1 + proj_length * line_unitvec

        return float(np.linalg.norm(p - closest_point))

    def simplify_rdp(self, points: np.ndarray, epsilon_meters: float = 8.0) -> np.ndarray:
        """
        Рекурсивное сжатие траектории.
        :param points: Массив координат shape (N, 2) -> [[lat, lon], ...]
        :param epsilon_meters: Максимально допустимое геометрическое отклонение (порог 8 метров)
        """
        if len(points) < 3:
            return points

        dmax = 0.0
        index = 0
        end = len(points) - 1

        for i in range(1, end):
            d = self._perpendicular_distance(points[i], points[0], points[end])
            if d > dmax:
                index = i
                dmax = d

        if dmax > epsilon_meters:
            rec_results1 = self.simplify_rdp(points[:index + 1], epsilon_meters)
            rec_results2 = self.simplify_rdp(points[index:], epsilon_meters)
            return np.vstack((rec_results1[:-1], rec_results2))
        else:
            return np.vstack((points[0], points[end]))

    def downsample_gps_stream(
        self, 
        coordinates: List[Dict[str, float]], 
        max_points_for_api: int = 50
    ) -> List[Dict[str, float]]:
        """
        Сжатие входящего потока GPS с гарантией непопадания под блокировку лимитов.
        """
        if len(coordinates) <= max_points_for_api:
            return coordinates

        raw_points = np.array([[c["lat"], c["lon"]] for c in coordinates], dtype=np.float64)
        
        # Запуск RDP с базовым порогом 10 метров
        simplified = self.simplify_rdp(raw_points, epsilon_meters=10.0)

        # Если после RDP точек все еще больше лимита — равномерный шаг прореживания
        if len(simplified) > max_points_for_api:
            step = int(np.ceil(len(simplified) / max_points_for_api))
            simplified = simplified[::step]

        return [{"lat": round(float(p[0]), 6), "lon": round(float(p[1]), 6)} for p in simplified]