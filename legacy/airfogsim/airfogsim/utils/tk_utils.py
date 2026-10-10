import xml.etree.ElementTree as ET
from PIL import Image, ImageTk
import osmnx as ox
from tqdm import tqdm
from pyproj import Proj, Transformer
import os 

def task_path_info(task_path):
    '''返回string类型描述task_path
    {
        'task':self.to_offload_tasks[task_idx], 
        'path':offload_objs, # 指向任务卸载的对象
        'mode':None, # X2X 的连接
        'Activated':False, # 当前的TTI是否被传输
    }选择task.id，查看path的每个objs，根据obj.type_name 判断是哪种类型的对象，然后根据obj.id，以及path是否超过了2个对像，返回一段string
    '''
    task = task_path['task']
    path = task_path['path']
    mode = task_path['mode']
    Activated = task_path['Activated']
    task_path_info = f"[{'Activated' if Activated else 'Deactivated'}] Task Vehicle {task.g_veh.id} -> Task {task.id} -> {path[0].type_name} {path[0].id}"
    return task_path_info


def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')
def get_map_data_from_sumo(traci_connection):
    edges = traci_connection.edge.getIDList()
    # 获取每条边的形状信息（即道路坐标）
    edge_shapes = {}
    for edge_id in edges:
        edge_shapes[edge_id] = traci_connection.edge.getShape(edge_id)
    return edge_shapes

def get_map_data_from_osm(osm_file, bbox, proj_params, netOffset):
    # Read the graph from the OSM file
    G = ox.graph_from_xml(osm_file, simplify=False, retain_all=True)

    # Convert the graph to a geopandas GeoDataFrame
    gdf_edges = ox.graph_to_gdfs(G, nodes=False)
    # gdf_edges = gdf_edges.to_crs(epsg=4326)
    utm_proj = Proj(proj_params)
    wgs84_proj = Proj(proj="longlat", datum='WGS84')
    transformer = Transformer.from_proj(wgs84_proj, utm_proj)
    # Extract road segments from the GeoDataFrame
    road_segments = []
    # 使用tqdm展示进度条
    print("从GeoDataFrame中提取道路段...")
    min_lon, min_lat, max_lon, max_lat = bbox
    for index, row in tqdm(gdf_edges.iterrows(), total=gdf_edges.shape[0]):
        for start, end in zip(row["geometry"].coords[:-1], row["geometry"].coords[1:]):
            x1, y1 = start
            x2, y2 = end
            # 如果道路段的两个端点都在 bbox 内，则将该道路段添加到列表中
            if min_lon <= x1 <= max_lon and min_lat <= y1 <= max_lat and min_lon <= x2 <= max_lon and min_lat <= y2 <= max_lat:
                x1, y1 = transformer.transform(x1, y1)
                x2, y2 = transformer.transform(x2, y2)
                x1, y1 = x1 + netOffset[0], y1 + netOffset[1]
                x2, y2 = x2 + netOffset[0], y2 + netOffset[1]
                road_segments.append(((x1, y1), (x2, y2)))
    return road_segments

def make_image_transparent(image_path, size=(20, 20), is_image = False):
    if is_image:
        image = image_path
    else:
        image = Image.open(image_path)
    if size != None:
        image_resized = image.resize(size, Image.Resampling.LANCZOS)
    else:
        image_resized = image
    image_resized = image_resized.convert("RGBA")

    transparent_image = Image.new("RGBA", image_resized.size, (0, 0, 0, 0))

    width, height = image_resized.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = image_resized.getpixel((x, y))
            if r >= 230 and g >= 230 and b >= 230:
                transparent_image.putpixel((x, y), (0, 0, 0, 0))
            else:
                transparent_image.putpixel((x, y), (r, g, b, a))

    return ImageTk.PhotoImage(transparent_image)

def create_rotated_images(image_path, num_rotations=36, size=(20,30)):
    original_image = Image.open(image_path).convert("RGBA").resize(size)
    rotated_images = []

    for i in range(num_rotations):
        angle = i * (360 / num_rotations)
        if angle > 90 and angle < 270:
            rotated_image = original_image
        else:
            rotated_image = original_image.transpose(Image.FLIP_LEFT_RIGHT)
        
        rotated_image = rotated_image.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=(0, 0, 0, 0))
        
        if angle > 90 and angle < 270:
            rotated_image = rotated_image.transpose(Image.FLIP_LEFT_RIGHT)
            rotated_image = rotated_image.transpose(Image.FLIP_TOP_BOTTOM)

        rotated_photoimage = make_image_transparent(rotated_image, size=None, is_image=True)
        rotated_images.append(rotated_photoimage)

    return rotated_images

def parse_location_info(file_path):
    tree = ET.parse(file_path)
    root = tree.getroot()
    location_element = root.find("location")

    conv_boundary = location_element.get("convBoundary")
    orig_boundary = location_element.get("origBoundary")
    proj_params = location_element.get("projParameter")
    netOffset = location_element.get("netOffset")
    # 把netOffset转换为元组
    netOffset = tuple(map(float, netOffset.split(",")))
    return conv_boundary, orig_boundary, proj_params, netOffset
    