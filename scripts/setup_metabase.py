"""Provision the local dashboard from SQL files and environment configuration."""
import json
import os
from pathlib import Path
import sys
from uuid import NAMESPACE_URL, uuid5

import requests
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
TITLE='Investment Pipeline | Synthetic Trading'
COLLECTION='Investment Pipeline | portfolio-v1'
DATABASE='Investment analytics | read-only'
PARAMETERS=[
    {'id':'start_date','name':'From (UTC, trades only)','slug':'from','type':'date/single','default':'2026-05-01'},
    {'id':'end_date','name':'To (UTC, trades only)','slug':'to','type':'date/single','default':'2026-10-27'},
    {'id':'asset','name':'Asset (trades only)','slug':'asset','type':'string/=',
     'values_query_type':'list','values_source_type':'static-list','values_source_config':{'values':[['BTC'],['ETH'],['SOL']]}}
]
CARDS=[
 ('trade_volume','Trade volume · USD','scalar',0,3,6,3),
 ('fees','Fees · USD','scalar',6,3,6,3),
 ('trade_count','Matched trades','scalar',12,3,6,3),
 ('active_customers','Distinct active customers','scalar',18,3,6,3),
 ('daily_volume','Daily trade volume · includes zero-trade days','line',0,6,12,7),
 ('daily_fees','Daily fees · includes zero-trade days','line',12,6,12,7),
 ('volume_asset_side','Trade volume by asset & BUY/SELL','bar',0,13,14,7),
 ('customer_segments','Customer segments · fixed 2026-10-27 snapshot','bar',14,13,10,7),
 ('top_customers_fees','Top customers by fees · fixed 2026-10-27 snapshot','table',0,20,24,8),
]


class Client:
    def __init__(self):
        load_dotenv(ROOT/'.env')
        self.base=f"http://127.0.0.1:{os.getenv('METABASE_PORT','3000')}"
        self.session=requests.Session()

    def call(self,method,path,payload=None):
        response=self.session.request(method,self.base+'/api'+path,json=payload,timeout=(10,120))
        if not response.ok:
            # Never print response body: APIs may reflect submitted connection details.
            raise RuntimeError(f'Metabase {method} {path} failed HTTP {response.status_code}')
        return response.json() if response.content else None

    def login(self,allow_setup=False):
        if allow_setup:
            token=self.call('GET','/session/properties').get('setup-token')
            if token:
                self.call('POST','/setup',{'token':token,'user':{
                    'first_name':'Portfolio','last_name':'Admin',
                    'email':os.environ['METABASE_ADMIN_EMAIL'],'password':os.environ['METABASE_ADMIN_PASSWORD']},
                    'prefs':{'site_name':'Investment Analytics · Synthetic Demo','site_locale':'en'}})
        session=self.call('POST','/session',{'username':os.environ['METABASE_ADMIN_EMAIL'],'password':os.environ['METABASE_ADMIN_PASSWORD']})
        self.session.headers['X-Metabase-Session']=session['id']


def entries(value):
    return value.get('data',[]) if isinstance(value,dict) else value


def tags(key):
    if key in ('customer_segments','top_customers_fees'):
        return {}
    return {name:{'id':str(uuid5(NAMESPACE_URL,'investment-dashboard/'+name)),
        'name':name,'display-name':label,'type':kind,'required':False,
        **({'default':default} if default else {})}
        for name,label,kind,default in [('start_date','From UTC','date','2026-05-01'),
                                      ('end_date','To UTC','date','2026-10-27'),('asset','Asset','text',None)]}


def visualization(key,display):
    settings={}
    if display=='scalar':
        settings={'scalar.decimals':2 if key in ('trade_volume','fees') else 0,
                  'scalar.scale':1,'scalar.locale':'en','scalar.separator_style':',.'}
        if key in ('trade_volume','fees'):
            field='trade_volume_usd' if key=='trade_volume' else 'fees_usd'
            settings['column_settings']={json.dumps(['name',field],separators=(',',':')):
                {'number_style':'currency','currency':'USD','currency_style':'symbol','decimals':2}}
    if display=='line':
        settings={'graph.dimensions':['trade_date'],'graph.metrics':['fees_usd' if key=='daily_fees' else 'trade_volume_usd'],
                  'graph.x_axis.title_text':'UTC day','graph.y_axis.title_text':'USD',
                  'graph.colors':['#16a394' if key=='daily_fees' else '#4169e1']}
    if key=='volume_asset_side':
        settings={'graph.dimensions':['asset','side'],'graph.metrics':['trade_volume_usd'],
                  'stackable.stack_type':'stacked','graph.y_axis.title_text':'Trade volume USD',
                  'graph.colors':['#4169e1','#16a394']}
    if key=='customer_segments':
        settings={'graph.dimensions':['activity_segment'],'graph.metrics':['customers'],
                  'graph.colors':['#16a394','#f2a93b','#8797ac'],'graph.y_axis.title_text':'Customers (unfiltered snapshot)'}
    return settings


def main():
    client=Client();client.login(allow_setup=True)
    client.call('PUT','/setting/report-timezone',{'value':'UTC'})
    databases=entries(client.call('GET','/database'))
    db=next((d for d in databases if d['name']==DATABASE),None)
    payload={'name':DATABASE,'engine':'postgres','is_full_sync':True,'details':{
        'host':'postgres','port':5432,'dbname':os.environ['POSTGRES_DB'],
        'user':'metabase_reader','password':os.environ['METABASE_READER_PASSWORD'],'ssl':False,
        'schema-filters-type':'inclusion','schema-filters-patterns':'analytics'}}
    db=client.call('PUT',f"/database/{db['id']}",payload) if db else client.call('POST','/database',payload)
    collections=entries(client.call('GET','/collection'))
    collection=next((c for c in collections if c['name']==COLLECTION),None)
    if not collection:
        collection=client.call('POST','/collection',{'name':COLLECTION,'description':'Versioned local synthetic trading demo; no real customer/execution data.'})
    items=entries(client.call('GET',f"/collection/{collection['id']}/items"))
    saved={i['name']:i for i in items}
    cards={}
    for key,name,display,_,_,_,_ in CARDS:
        payload={'name':name,'collection_id':collection['id'],'display':display,
            'description':'Synthetic executions/customers; Coinbase reference closes. portfolio-v1, cutoff 2026-10-27 UTC.',
            'dataset_query':{'database':db['id'],'type':'native','native':{
                'query':(ROOT/'metabase/cards'/f'{key}.sql').read_text(),'template-tags':tags(key)}},
            'visualization_settings':visualization(key,display)}
        existing=saved.get(name)
        card=client.call('PUT',f"/card/{existing['id']}",payload) if existing else client.call('POST','/card',payload)
        cards[key]=card['id']
    existing=saved.get(TITLE)
    payload={'name':TITLE,'collection_id':collection['id'],'parameters':PARAMETERS,
             'description':'SYNTHETIC customers & executions | Coinbase daily-close reference | portfolio-v1 | cutoff 2026-10-27 UTC. Filters apply only to trading cards; customer cards remain fixed snapshots.'}
    dashboard=client.call('PUT',f"/dashboard/{existing['id']}",payload) if existing else client.call('POST','/dashboard',payload)
    # Reuse placement IDs so reruns update the layout without adding duplicate cards.
    current=client.call('GET',f"/dashboard/{dashboard['id']}")
    old={d.get('card_id'):d['id'] for d in current.get('dashcards',[])}
    banner=('## SYNTHETIC TRADING · Portfolio analytics\n'
        '**Synthetic customers & matched executions** · Coinbase daily-close reference prices  \n'
        '**portfolio-v1 · UTC cutoff 2026-10-27** · Trade volume ≠ revenue  \n'
        'Date / asset filters apply to trading cards only. Customer segments and top-fee customers stay at the fixed cutoff snapshot.')
    placements=[{'id':old.get(None,-1),'card_id':None,'row':0,'col':0,'size_x':24,'size_y':3,
        'visualization_settings':{'virtual_card':{'display':'text','visualization_settings':{},'dataset_query':{}},'text':banner},'parameter_mappings':[]}]
    for index,(key,_,_,col,row,width,height) in enumerate(CARDS,2):
        mappings=[{'parameter_id':name,'card_id':cards[key],
            'target':['variable',['template-tag',name]]} for name in tags(key)]
        placements.append({'id':old.get(cards[key],-index),'card_id':cards[key],
            'row':row,'col':col,'size_x':width,'size_y':height,'parameter_mappings':mappings})
    client.call('PUT',f"/dashboard/{dashboard['id']}",{'dashcards':placements,'width':'full','auto_apply_filters':True})
    state={'dashboard_id':dashboard['id'],'collection_id':collection['id'],'database_id':db['id'],'cards':cards,
           'url':f"http://localhost:{os.getenv('METABASE_PORT','3000')}/dashboard/{dashboard['id']}"}
    (ROOT/'data').mkdir(exist_ok=True)
    (ROOT/'data/metabase-state.json').write_text(json.dumps(state,indent=2))
    print(json.dumps(state,indent=2))


if __name__=='__main__':
    main()
