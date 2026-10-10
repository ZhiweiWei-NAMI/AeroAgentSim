import sympy as sp
import timeit

# 直接符号运算
x, y = sp.symbols('x y')
expr = x + y

def direct_symbolic_computation():
    subs = {x: 2, y: 3}
    return expr.evalf(subs=subs)

expr_str = "x + y"
expr = sp.sympify(expr_str)
# 使用 sympify
def sympify_computation():
    global expr
    subs = {sp.symbols('x'): 2, sp.symbols('y'): 3}
    return expr.evalf(subs=subs)

# 测量性能
direct_time = timeit.timeit(direct_symbolic_computation, number=1000)
sympify_time = timeit.timeit(sympify_computation, number=1000)

print(f"Direct symbolic computation time: {direct_time} seconds")
print(f"Sympify computation time: {sympify_time} seconds")
