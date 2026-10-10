
import random

def property_generator(cls):
    for name, value in vars(cls).items():
        if isinstance(value, property):
            continue
        if name.startswith("_"):
            prop_name = name[1:]
            getter = property(lambda self, name=name: getattr(self, name))
            setter = getter.setter(lambda self, value, name=name: setattr(self, name, value))
            setattr(cls, prop_name, getter.setter(setter))
    return cls

class RandomColorGenerator:
    def __init__(self, n, seed=0):
        self.colors = []
        random.seed(seed)
        for _ in range(n):
            color = "#{:02x}{:02x}{:02x}".format(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
            self.colors.append(color)

    def get_color(self, index):
        if isinstance(index, str):
            index = hash(index)
        index = index % len(self.colors)
        color = self.colors[index]
        return color
    


def random_colors_generator(n, seed=0):
    """
    生成 n 个随机颜色

    :param n: 生成颜色的数量
    :return: 一个generator，接收索引，返回颜色，可以超过 n，超过的部分会重复
    """
    return RandomColorGenerator(n, seed=seed)


if __name__ == '__main__':
    a = random_colors_generator(10)
    print(a.get_color(1))
    print(a.get_color("v2v_test"))
    print(a.get_color("v2v_test"))
    print(a.get_color("v2v_test2"))