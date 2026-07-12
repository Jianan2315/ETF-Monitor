from src.position import calculate_position

def test_position_from_transactions():
    tx=[{'date':'2026-01-01','type':'BUY','amount':10000,'price':1.0},{'date':'2026-02-01','type':'BUY','amount':5000,'price':0.5}]
    p=calculate_position(tx,1.0)
    assert p['invested_amount']==15000
    assert p['shares']==20000
    assert p['average_cost']==0.75
    assert p['profit_amount']==5000

def test_sell_reduces_cost_basis():
    tx=[{'date':'2026-01-01','type':'BUY','amount':10000,'price':1.0},{'date':'2026-02-01','type':'SELL','amount':2400,'price':1.2,'shares':2000}]
    p=calculate_position(tx,1.1)
    assert p['shares']==8000
    assert p['invested_amount']==8000
