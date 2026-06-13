import sys, time

for i in range(5):
    sys.stdout.write("\rTEST %d" % i)
    sys.stdout.flush()
    time.sleep(1)

sys.stdout.write("\rDONE   \n")
sys.stdout.flush()