"""Synthetic robustness tests: no real company files, answer keys or API calls."""
import asyncio
import sqlite3
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hw3

GOOD_SALES = 'Customers trust the knowledgeable sales team to solve technical problems.'
GOOD_LABOUR = 'Staff are treated fairly and workloads are manageable.'

class FakeTool:
    def __init__(self, name, result):
        self.name, self.result, self.calls = name, result, []
    async def ainvoke(self, args):
        self.calls.append(args)
        if isinstance(self.result, Exception):
            raise self.result
        if callable(self.result):
            return self.result(args)
        return self.result

class FakeAgent:
    def __init__(self, mode='normal'):
        self.mode, self.calls = mode, []
    async def ainvoke(self, args):
        self.calls.append(args)
        if self.mode == 'failure':
            raise RuntimeError('simulated API outage')
        result = {
            '4': {'verdict':'PASS', 'sufficient':True, 'quote':GOOD_SALES, 'reason':'Evidence of helpful sales support.'},
            '7': {'verdict':'PASS', 'sufficient':True, 'quote':GOOD_LABOUR, 'reason':'Fair treatment and manageable work.'},
        }
        if self.mode == 'invented':
            result['4']['quote'] = 'This fabricated source quote must be rejected.'
        if self.mode == 'uncertain' and len(self.calls) == 1:
            result['4']['sufficient'] = False
        return result

def margin_output(revenue=100, costs=90, months=12, median=10):
    # Synthetic in-memory data exercises the real SQL without opening company.db.
    with sqlite3.connect(':memory:') as db:
        db.executescript('''CREATE TABLE orders(order_date TEXT, amount REAL);
        CREATE TABLE monthly_costs(month TEXT,cost_of_sales REAL,selling_admin REAL,research_development REAL);
        CREATE TABLE company(name TEXT,industry TEXT);
        CREATE TABLE industry_medians(industry TEXT,operating_margin_pct REAL);''')
        db.executemany('INSERT INTO orders VALUES (?,?)',[('2025-03-01',revenue),('2026-03-01',9999)])
        db.executemany('INSERT INTO monthly_costs VALUES (?,?,?,?)',[(f'2025-{m:02d}',costs/months,0,0) for m in range(1,months+1)])
        db.execute("INSERT INTO company VALUES ('Unseen Company','test')")
        db.executemany('INSERT INTO industry_medians VALUES (?,?)',[('test',median),('other',99)])
        cursor=db.execute(hw3.MARGIN_QUERY)
        names=[x[0] for x in cursor.description]
        values=cursor.fetchone()
        return ' | '.join(names)+'\n'+' | '.join(str(x) for x in values)

def fake_tools(sql=None):
    sql = margin_output() if sql is None else sql
    interview=FakeTool('interview', lambda a: GOOD_SALES if a['topic']=='sales_team' else GOOD_LABOUR)
    return [FakeTool('list_documents',''),FakeTool('read_document',''),FakeTool('sql_query',sql),interview]

class AgentTests(unittest.TestCase):
    def test_margin_boundary_and_industry_filter(self):
        self.assertEqual(hw3._parse_margin(margin_output())['verdict'],'PASS')
        self.assertEqual(hw3._parse_margin(margin_output(costs=90.01))['verdict'],'FAIL')
        self.assertEqual(hw3._parse_margin(margin_output(costs=110,median=-20))['verdict'],'FAIL')
    def test_missing_financial_evidence_is_not_a_valid_row(self):
        for raw in [margin_output(revenue=0),margin_output(months=11),'SQL error: absent table']:
            with self.assertRaises(ValueError): hw3._parse_margin(raw)
    def test_normal_uses_only_two_interviews(self):
        tools=fake_tools()
        out=asyncio.run(hw3.analyze_companies(FakeAgent(),{'new_name':tools}))
        self.assertEqual(out['new_name']['points'],{4:'PASS',5:'PASS',7:'PASS'})
        self.assertEqual(len(tools[-1].calls),2)
        self.assertFalse(tools[0].calls or tools[1].calls)
    def test_uncertainty_uses_correct_backup_without_recollecting(self):
        tools=fake_tools();agent=FakeAgent('uncertain')
        out=asyncio.run(hw3.analyze_companies(agent,{'unseen':tools}))
        self.assertEqual(out['unseen']['points'][4],'PASS')
        self.assertEqual(len(tools[-1].calls),3)
        self.assertEqual(tools[-1].calls[-1],{'role':'competitor','topic':'sales_team'})
    def test_api_outage_retains_sql_and_returns_every_company(self):
        out=asyncio.run(hw3.analyze_companies(FakeAgent('failure'),{'one':fake_tools(),'two':fake_tools()}))
        self.assertEqual(set(out),{'one','two'})
        for r in out.values():
            self.assertEqual(r['points'],{4:'FAIL',5:'PASS',7:'FAIL'})
            self.assertIn('operational fallback',r['summary'])
    def test_fabricated_quote_rejected(self):
        agent=FakeAgent('invented'); tools=fake_tools()
        out=asyncio.run(hw3.analyze_companies(agent,{'unseen':tools}))
        self.assertEqual(out['unseen']['points'][4],'FAIL')
        self.assertIn('fallback',out['unseen']['summary'])
        self.assertEqual(len(agent.calls),2)
        self.assertEqual(len(tools[-1].calls),2)
    def test_sql_error_does_not_discard_interviews(self):
        out=asyncio.run(hw3.analyze_companies(FakeAgent(),{'broken':fake_tools('SQL error: simulated'),'good':fake_tools()}))
        self.assertEqual(out['broken']['points'],{4:'PASS',5:'FAIL',7:'PASS'})
        self.assertEqual(out['good']['points'],{4:'PASS',5:'PASS',7:'PASS'})

if __name__=='__main__': unittest.main()
