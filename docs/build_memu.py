import json
import os

def build_menu_json_string():
    current_dir = os.getcwd()
    print(current_dir)

    # [{"name": "kafka", "items": [{"name": "Consumer", "items": [{"name": "test"}]}, {"name": "ConsumerCoordinator.md"}]}]
    level_1 = []
    for dir_name in os.listdir(current_dir):
        if dir_name == '.':
            continue
        if os.path.isdir(dir_name):
            level_2 = []
            level_1.append({"name": dir_name, "items": level_2})
            for files2 in os.listdir(dir_name):
                files2_path = os.path.join(current_dir, dir_name, files2)
                if os.path.isdir(files2_path):
                    level_3 = []
                    level_2.append({"name": files2, "items": level_3})
                    for files3 in os.listdir(files2_path):
                        level_3.append({"name": files3})
                else:
                    level_2.append({"name": files2})

    return json.dumps(level_1)


if __name__ == "__main__":
    result = build_menu_json_string()
    print(result)
    with open('./menu', 'w', encoding='utf-8') as file:
        file.write(result)