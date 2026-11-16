import time,sys
K=["TcpExt:TCPToZeroWindowAdv","TcpExt:TCPWantZeroWindowAdv","TcpExt:PruneCalled","TcpExt:PAWSEstab","TcpExt:TCPDSACKOldSent","TcpExt:TCPOFOQueue","Tcp:InSegs","TcpExt:TCPRcvCollapsed"]
def rd():
    d={}
    for fn in ("/proc/net/netstat","/proc/net/snmp"):
        L=[l for l in open(fn).read().split("\n") if l]
        for i in range(0,len(L)-1,2):
            h=L[i].split(); v=L[i+1].split()
            for a,b in zip(h[1:],v[1:]):
                try: d[h[0]+a]=int(b)
                except: pass
    return d
a=rd(); time.sleep(int(sys.argv[1])); b=rd()
for k in K: print(k, b[k]-a[k])
