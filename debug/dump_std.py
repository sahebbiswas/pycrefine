import dis

def show_info(x: int) -> int:
    for i in range(x):
        print(2*i)
    return 2%x == 0

dis.dis(show_info)
