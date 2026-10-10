from inspect import isfunction, getfullargspec


class ValidateStaticMethods(type):
    def __new__(cls, name, bases, attrs):
        for key, value in attrs.items():
            if isfunction(value):  # 检查是否为函数
                # 检查是否为静态方法
                if not hasattr(value, '__self__') or value.__self__ is not None:
                    raise TypeError(f"Method {key} must be a static method")
                
                # 检查第一个参数是否为 AirFogSimEnv 类的实例
                args = getfullargspec(value.__func__).args
                if len(args) == 0 or args[0] != 'env' or not hasattr(value.__func__, '__annotations__'):
                    raise TypeError(f"The first argument of method {key} must be 'env: AirFogSimEnv'")


        return super().__new__(cls, name, bases, attrs)
    