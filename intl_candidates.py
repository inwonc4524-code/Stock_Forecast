"""
해외 9개 나라 후보 목록 — update_universe.py가 이 중에서 시가총액 상위 50개씩을 골라 stocks.csv에 더해요.
형식: "티커:테마" (테마는 비워도 돼요 → 업종을 보고 자동으로 붙여요)
테마 key: semi 반도체 · bat 2차전지 · ess 전고체·ESS · power AI 전력 · robot 로봇
          bio 바이오·제약 · defense 방산·조선 · fin 금융 · net 인터넷·게임·엔터
후보를 더 넣거나 빼고 싶으면 이 파일만 고치면 돼요.
"""

CANDIDATES = {
    # 🇭🇰 홍콩 (.HK)
    "HK": """
0700.HK:net 9988.HK:net 3690.HK:net 1299.HK:fin 0939.HK:fin 1398.HK:fin 3988.HK:fin 0005.HK:fin
0941.HK 2318.HK:fin 1810.HK:net 9618.HK:net 9999.HK:net 1211.HK:bat|ess 0388.HK:fin 0883.HK 0857.HK
0386.HK 1088.HK 2628.HK:fin 0016.HK 1113.HK 0001.HK 0002.HK:power 0003.HK 0006.HK:power 0011.HK:fin
0027.HK 1928.HK 0267.HK 0688.HK 1109.HK 2020.HK 0291.HK 2319.HK 1876.HK 9888.HK:net 9961.HK:net
1024.HK:net 2015.HK:bat 9868.HK:bat 9866.HK:bat 0981.HK:semi 1347.HK:semi 2382.HK 0992.HK 0175.HK
2269.HK:bio 1177.HK:bio 1093.HK:bio 6160.HK:bio 1801.HK:bio 3692.HK:bio 0066.HK 0823.HK 2388.HK:fin
3968.HK:fin 0762.HK 0728.HK 0836.HK:power 0902.HK:power 9633.HK 6690.HK 2899.HK 1919.HK
3750.HK:bat|ess 9880.HK:robot 6618.HK:bio 0268.HK:net 0020.HK:robot
""",
    # 🇨🇳 중국 본토 (.SS 상하이 · .SZ 선전)
    "CN": """
600519.SS 300750.SZ:bat|ess 601318.SS:fin 600036.SS:fin 000858.SZ 601398.SS:fin 601288.SS:fin
601988.SS:fin 601939.SS:fin 600900.SS:power 601857.SS 600028.SS 601088.SS 002594.SZ:bat|ess
600276.SS:bio 000333.SZ:robot 000651.SZ 601012.SS:ess 300059.SZ:fin 600030.SS:fin 601166.SS:fin
600000.SS:fin 601328.SS:fin 601628.SS:fin 601601.SS:fin 600309.SS 002415.SZ 300760.SZ:bio 600887.SS
603288.SS 000568.SZ 600809.SS 002475.SZ 688981.SS:semi 688041.SS:semi 688256.SS:semi 603501.SS:semi
002371.SZ:semi 688012.SS:semi 300308.SZ:semi 300502.SZ:semi 601138.SS 000063.SZ 600941.SS 601728.SS
600050.SS 601899.SS 600585.SS 601668.SS 601390.SS 601766.SS 600031.SS:robot 000725.SZ
002466.SZ:bat 002460.SZ:bat 300014.SZ:bat|ess 300274.SZ:ess|power 601985.SS:power 003816.SZ:power
600150.SS:defense 600760.SS:defense 600893.SS:defense 300124.SZ:robot 002050.SZ:robot 601689.SS:robot
""",
    # 🇯🇵 일본 (.T)
    "JP": """
7203.T 6758.T:net 8306.T:fin 6861.T:robot 9983.T 9984.T:net 6501.T:power 8035.T:semi 9432.T 9433.T
8316.T:fin 8411.T:fin 4063.T:semi 6098.T:net 7974.T:net 4519.T:bio 4502.T:bio 4568.T:bio 6367.T
8058.T 8001.T 8031.T 8053.T 8002.T 7011.T:defense|power 7012.T:defense 7013.T:defense 6902.T 7267.T
7741.T 6857.T:semi 6146.T:semi 6920.T:semi 6723.T:semi 4661.T 9022.T 9020.T 8766.T:fin 8725.T:fin
8630.T:fin 8591.T:fin 6954.T:robot 6506.T:robot 6273.T:robot 6324.T:robot 6268.T:robot 6752.T:bat
6762.T 6981.T 6971.T 5108.T 2914.T 4452.T 3382.T 4307.T 6702.T 6701.T:defense 9434.T 9501.T:power
9503.T:power 285A.T:semi 6526.T:semi 4503.T:bio
""",
    # 🇻🇳 베트남 (.VN)
    "VN": """
VCB.VN:fin BID.VN:fin CTG.VN:fin VIC.VN VHM.VN VNM.VN HPG.VN FPT.VN:net GAS.VN MSN.VN TCB.VN:fin
VPB.VN:fin MBB.VN:fin ACB.VN:fin MWG.VN SAB.VN VRE.VN PLX.VN POW.VN:power GVR.VN BCM.VN SSI.VN:fin
HDB.VN:fin STB.VN:fin VJC.VN PNJ.VN REE.VN:power DGC.VN VIB.VN:fin LPB.VN:fin SHB.VN:fin TPB.VN:fin
BVH.VN:fin KDH.VN NVL.VN PDR.VN DCM.VN DPM.VN GMD.VN HVN.VN VND.VN:fin VCI.VN:fin KBC.VN DIG.VN
NLG.VN PVD.VN SSB.VN:fin OCB.VN:fin EIB.VN:fin VGC.VN BMP.VN
""",
    # 🇮🇩 인도네시아 (.JK)
    "ID": """
BBCA.JK:fin BBRI.JK:fin BMRI.JK:fin BBNI.JK:fin TLKM.JK ASII.JK AMMN.JK BREN.JK:power TPIA.JK
BYAN.JK ICBP.JK INDF.JK UNVR.JK KLBF.JK:bio PTBA.JK ITMG.JK ANTM.JK:bat INCO.JK:bat MDKA.JK:bat
UNTR.JK CPIN.JK GOTO.JK:net BRPT.JK PGAS.JK SMGR.JK INTP.JK AMRT.JK MAPI.JK ACES.JK EXCL.JK ISAT.JK
TOWR.JK MTEL.JK JPFA.JK MYOR.JK HMSP.JK GGRM.JK SIDO.JK:bio BUKA.JK:net ARTO.JK:fin BRIS.JK:fin
MEDC.JK ESSA.JK PGEO.JK:power CUAN.JK DSSA.JK SRTG.JK AKRA.JK MIKA.JK:bio NCKL.JK:bat AADI.JK
""",
    # 🇩🇪 독일 (.DE)
    "DE": """
SAP.DE:net SIE.DE:robot|power ALV.DE:fin DTE.DE MBG.DE BMW.DE VOW3.DE BAS.DE BAYN.DE:bio MUV2.DE:fin
DBK.DE:fin IFX.DE:semi ADS.DE DHL.DE RWE.DE:power EOAN.DE:power ENR.DE:power RHM.DE:defense
HNR1.DE:fin BEI.DE HEN3.DE MRK.DE:bio|semi FRE.DE:bio FME.DE:bio SHL.DE:bio DB1.DE:fin CBK.DE:fin
AIR.DE:defense MTX.DE:defense HEI.DE P911.DE PAH3.DE CON.DE SY1.DE VNA.DE ZAL.DE:net QIA.DE:bio
SRT3.DE:bio BNR.DE DTG.DE HAG.DE:defense TKA.DE:defense RENK.DE:defense KGX.DE:robot
JEN.DE:semi AIXA.DE:semi G1A.DE EVK.DE PUM.DE HLAG.DE TLX.DE:fin
""",
    # 🇬🇧 영국 (.L)
    "UK": """
AZN.L:bio SHEL.L HSBA.L:fin ULVR.L RIO.L BP.L GSK.L:bio REL.L LSEG.L:fin DGE.L BATS.L RR.L:defense
BA.L:defense NG.L:power BARC.L:fin LLOY.L:fin NWG.L:fin STAN.L:fin AAL.L GLEN.L:bat CPG.L EXPN.L
TSCO.L PRU.L:fin LGEN.L:fin AV.L:fin HLN.L:bio NXT.L ABF.L IHG.L HLMA.L SSE.L:power CRDA.L IMB.L
ANTO.L BT-A.L VOD.L AHT.L BNZL.L INF.L WPP.L SGRO.L LAND.L RKT.L SMIN.L AUTO.L:net III.L:fin
ADM.L:fin CNA.L:power SN.L:bio BKG.L PSN.L MNG.L:fin QQ.L:defense BAB.L:defense CHG.L:defense
OXIG.L:semi
""",
    # 🇸🇬 싱가포르 (.SI)
    "SG": """
D05.SI:fin O39.SI:fin U11.SI:fin Z74.SI C38U.SI A17U.SI S68.SI:fin BN4.SI:power S63.SI:defense
C6L.SI G13.SI F34.SI Y92.SI U96.SI:power 5E2.SI:defense C52.SI V03.SI S58.SI 9CI.SI N2IU.SI M44U.SI
ME8U.SI BS6.SI:defense H78.SI J36.SI C07.SI U14.SI C09.SI BUOU.SI J69U.SI AJBU.SI:power T82U.SI
K71U.SI D01.SI AIY.SI:fin 558.SI:semi AWX.SI:semi OYY.SI CJLU.SI S59.SI:defense E5H.SI
""",
    # 🇨🇦 캐나다 (.TO)
    "CA": """
RY.TO:fin TD.TO:fin SHOP.TO:net ENB.TO BN.TO:fin CNR.TO CP.TO BMO.TO:fin BNS.TO:fin CNQ.TO CM.TO:fin
TRI.TO CSU.TO:net ATD.TO SU.TO MFC.TO:fin TRP.TO WCN.TO ABX.TO AEM.TO FNV.TO WPM.TO NTR.TO
SLF.TO:fin IFC.TO:fin L.TO BCE.TO T.TO GIB-A.TO:net DOL.TO QSR.TO CCO.TO:power CLS.TO:semi
FTS.TO:power EMA.TO:power H.TO:power BAM.TO:fin POW.TO:fin GWO.TO:fin FFH.TO:fin TECK-B.TO
CVE.TO IMO.TO PPL.TO MG.TO WSP.TO CAE.TO:defense BBD-B.TO:defense NPI.TO:power BEP-UN.TO:power
LUN.TO FM.TO K.TO OTEX.TO:net DSG.TO:net
""",
}

MARKET_INFO = {   # 나라 코드: (국기, 이름)
    "HK": ("🇭🇰", "홍콩"), "CN": ("🇨🇳", "중국"), "JP": ("🇯🇵", "일본"), "VN": ("🇻🇳", "베트남"),
    "ID": ("🇮🇩", "인도네시아"), "DE": ("🇩🇪", "독일"), "UK": ("🇬🇧", "영국"), "SG": ("🇸🇬", "싱가포르"),
    "CA": ("🇨🇦", "캐나다"),
}


def candidates():
    """{나라: [(티커, 테마), ...]}"""
    out = {}
    for mk, text in CANDIDATES.items():
        items = []
        for tok in text.split():
            t, _, th = tok.partition(":")
            items.append((t, th))
        out[mk] = items
    return out
