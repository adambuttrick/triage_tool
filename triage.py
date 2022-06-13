import re
import csv
import sys
import itertools
import urllib.parse
from os import getcwd
from sys import argv
import requests
from thefuzz import fuzz
from scholarly import scholarly
from scholarly import ProxyGenerator
from bs4 import BeautifulSoup

GITHUB= {}
GITHUB['USER'] = ''
GITHUB['TOKEN'] = ''

def get_issue_comments(comments_url):
    comments = requests.get(comments_url, auth=(GITHUB['USER'], GITHUB['TOKEN'])).json()
    if comments != []:
        comments_text = []
        for comment in comments:
            text = comment['body']
            comments_text.append(text)
        return ' '.join(comments_text)
    else:
        return ''

def normalize_name(org_name):
    org_name = org_name.lower()
    org_name = re.sub(r'[^\w\s]', '', org_name)
    return org_name


def check_existing_issues(org_name, ror_id=None):
    print('Searching existing issues in Github...')
    rejected_orgs = {}
    pages = [str(i) for i in range(1,10)]
    states = ['open', 'closed']
    in_issues = []
    base_url = 'https://api.github.com/repos/ror-community/ror-updates/issues?state='
    for state in states:
        for page in pages:
            params = {'per_page': 100, 'page': page}
            state_url = 'https://api.github.com/repos/ror-community/ror-updates/issues?state=' + state
            api_response = requests.get(
                state_url, params=params, auth=(GITHUB['USER'], GITHUB['TOKEN'])).json()
            for issue in api_response:
                issue_html_url = issue['html_url']
                if ror_id:
                    issue_api_url = issue['url']
                    comments_url = issue_api_url + '/comments'
                    issue_text = issue['body'] + get_issue_comments(comments_url)
                    if ror_id in issue_text:
                        in_issues.append(issue_html_url)
                issue_number = issue['number']
                issue_title = issue['title']
                issue_title = re.sub('\n', '', issue_title)
                issue_title = re.sub('()', '', issue_title)
                labels = issue['labels']
                label_data = []
                for label in labels:
                    label_data.append(label['name'])
                if 'new record' in label_data:
                    try:
                        pattern = re.compile(r'(?<=\:)(.*)($)')
                        title_name = pattern.search(issue_title).group(0).strip()
                        rejected_orgs[issue_number] = {
                            'title_name': title_name, 'html_url': issue_html_url}
                    except AttributeError:
                        print('Unable to check against issue#', issue_number, '- title cannot be parsed')
    for key, value in rejected_orgs.items():
        mr = fuzz.ratio(normalize_name(org_name), normalize_name(value['title_name']))
        if mr > 90:
            print(org_name, 'was already requested or previously rejected. See issue#',
                  key, "at", value['html_url'])
    if in_issues != []:
        return in_issues
    else:
        return None


def search_wikidata(org_name):
    print('Searching Wikidata for', org_name, '...')
    url = 'https://www.wikidata.org/w/api.php?action=wbsearchentities&search=' + \
        urllib.parse.quote_plus(org_name) + '&language=en&format=json'
    api_response = requests.get(url).json()
    return api_response


def find_most_similar_wikidata_id(org_name, api_response):
    best_match_ratio = 0
    wikidata_label, wikidata_id = '', ''
    search_results = api_response['search']
    for result in search_results:
        match_ratio = fuzz.ratio(org_name, result['label'])
        if match_ratio > best_match_ratio:
            wikidata_id, wikidata_label = result['id'], result['label']
            best_match_ratio = match_ratio
    print(org_name, 'matched:', wikidata_label,
          'w/ match ratio of', str(match_ratio) + '%')
    return wikidata_label, wikidata_id, best_match_ratio


def get_wikipedia_url_from_wikidata_id(wikidata_id, lang='en'):
    url = 'https://www.wikidata.org/w/api.php?action=wbgetentities'
    params = {'props': 'sitelinks/urls', 'ids': wikidata_id, 'format': 'json'}
    api_response = requests.get(url, params=params).json()
    if 'sitelinks' in api_response['entities'][wikidata_id]:
        try:
            wikipedia_url = api_response['entities'][wikidata_id]['sitelinks'][lang + 'wiki']['url']
            return wikipedia_url
        except KeyError:
            return None
    else:
        return None


def get_location_entity(wikidata_id):
    url = 'https://www.wikidata.org/w/api.php?action=wbgetentities'
    params = {'ids': wikidata_id, 'format': 'json'}
    api_response = requests.get(url, params=params).json()
    claims = api_response['entities'][wikidata_id]['claims']
    geonames_id = ''
    if 'P1566' in claims:
        geonames_id = claims['P1566'][0]['mainsnak']['datavalue']['value']
    if 'en' in api_response['entities'][wikidata_id]['labels']:
        location_name = api_response['entities'][wikidata_id]['labels']['en']['value']
        return location_name, geonames_id
    else:
        return None


def funder_id_search(org_name):
    url = 'https://api.crossref.org/funders?query=' + \
        urllib.parse.quote_plus(org_name)
    api_response = requests.get(url).json()
    if api_response['message']['items'] == []:
        return None
    else:
        best_match_ratio = 0
        crossref_funder_id = ''
        for item in api_response['message']['items']:
            match_ratio = fuzz.ratio(org_name, item['name'])
            if match_ratio > .9 and match_ratio > best_match_ratio:
                crossref_funder_id = item['id']
                best_match_ratio = match_ratio
            elif org_name in item['alt-names']:
                crossref_funder_id = item['id']
        return crossref_funder_id


def google_scholar_search(org_name):
    # uncomment if Google blocks IP
    # pg = ProxyGenerator()
    # pg.FreeProxies()
    # scholarly.use_proxy(pg)
    affiliated_google_scholar_urls = []
    search_query = scholarly.search_author(org_name)
    for result in search_query:
        if len(affiliated_google_scholar_urls) > 2:
            return affiliated_google_scholar_urls
        else:
            scholar_data = scholarly.fill(
                result, sections=['basics', 'citations'])
            if org_name in scholar_data['affiliation']:
                scholar_id = scholar_data['scholar_id']
                scholar_url = 'https://scholar.google.com/citations?user=' + scholar_id
                affiliated_google_scholar_urls.append(scholar_url)
    return affiliated_google_scholar_urls


def orcid_search(org_name):
    orcid_urls = []
    search_url = 'https://pub.orcid.org/v3.0/expanded-search/?q=affiliation-org-name:"' + \
        org_name + '"&fl=orcid,current-institution-affiliation-name,past-institution-affiliation-name'
    r = requests.get(search_url)
    soup = BeautifulSoup(r.text, 'lxml')
    head_tag = soup.find('expanded-search:expanded-search')
    num_found = head_tag['num-found']
    if num_found != '0':
        orcid_id_tags = soup.find_all('expanded-search:orcid-id')
        for tag in orcid_id_tags:
            orcid_id = tag.text
            orcid_url = 'https://orcid.org/'+orcid_id
            orcid_urls.append(orcid_url)
        if len(orcid_urls) >= 3:
            return orcid_urls[0:3]
        else:
            return orcid_urls
    else:
        return []


def clean_org_name(org_name):
    org_name = org_name.lower()
    return org_name

def ror_search(org_name):
    query_url = 'https://api.ror.org/organizations?query="' + \
       org_name + '"'
    affiliation_url =  'https://api.ror.org/organizations?affiliation="' + \
       org_name + '"'
    all_urls = [query_url, affiliation_url]
    ror_matches = []
    for url in all_urls:
        api_response = requests.get(url).json()
        if api_response['number_of_results'] != 0:
            results = api_response['items']
            for result in results:
                if 'organization' in result.keys():
                    result = result['organization']
                ror_id = result['id']
                ror_name = result['name']
                aliases = result['aliases']
                labels = []
                if result['labels'] != []:
                    labels = [label['label'] for label in result['labels']]
                name_mr = fuzz.ratio(clean_org_name(org_name), clean_org_name(ror_name))
                if name_mr >= 90:
                    match_type = 'name match'
                    ror_matches.append([ror_id, ror_name, match_type])
                elif org_name in aliases:
                    match_type = 'alias match'
                    ror_matches.append([ror_id, ror_name, match_type])
                elif org_name in labels:
                    match_type = 'label match'
                    ror_matches.append([ror_id, ror_name, match_type])
                elif 'relationships' in result:
                    for relationship in result['relationships']:
                        if org_name in relationship['label']:
                            match_type = 'relationship'
                            ror_matches.append([ror_id, ror_name, match_type])
    ror_matches = list(ror_matches for ror_matches,_ in itertools.groupby(ror_matches))
    if ror_matches == []:
        print("No matches in ROR found for", org_name)
    else:
        for match in ror_matches:
            print("Found existing record in ROR", match[0], "-", match[1])
    return ror_matches


def get_wikidata(org_name, wikidata_id, match_ratio):
    wikidata_entry = {}
    url = 'https://www.wikidata.org/w/api.php?action=wbgetentities&ids=' + \
        wikidata_id + '&format=json'
    api_response = requests.get(url).json()
    if 'entities' not in api_response:
        print('No entities found for', org_name)
        return []
    wikidata_entry['name'] = org_name
    wikidata_entry['wikidata_id'] = wikidata_id
    wikidata_entry['name_match_ratio'] = match_ratio
    if 'aliases' in api_response['entities'][wikidata_id]:
        all_aliases = []
        aliases = api_response['entities'][wikidata_id]['aliases'].values()
        for alias in aliases:
            for a in alias:
                all_aliases.append(a['value'])
        if len(all_aliases) > 0:
            wikidata_entry['labels'] = all_aliases
    wikipedia_url = get_wikipedia_url_from_wikidata_id(wikidata_id)
    if wikipedia_url is not None:
        wikidata_entry['wikipedia_url'] = wikipedia_url
    try:
        claims = api_response['entities'][wikidata_id]['claims']
        if 'P571' in claims:
            year_established = claims['P571'][0]['mainsnak']['datavalue']['value']['time']
            year_established = year_established[1:5]
            wikidata_entry['established'] = year_established
        if 'P131' in claims:
            admin_terr_entity = claims['P131'][0]['mainsnak']['datavalue']['value']['id']
            admin_terr_data = get_location_entity(admin_terr_entity)
            if admin_terr_data is not None:
                wikidata_entry['admin_terr_name'] = admin_terr_data[0]
                wikidata_entry['admin_terr_geonames_id'] = admin_terr_data[1]
        if 'P276' in claims:
            city_entity = claims['P276'][0]['mainsnak']['datavalue']['value']['id']
            city_data = get_location_entity(city_entity)
            if city_data is not None:
                wikidata_entry['city'] = city_data[0]
                wikidata_entry['city_geonames_id'] = city_data[1]
        if 'P17' in claims:
            country_entity = claims['P17'][0]['mainsnak']['datavalue']['value']['id']
            country_name = get_location_entity(country_entity)
            if country_name is not None:
                wikidata_entry['country'] = country_name[0]
        if 'P625' in claims:
            location = claims['P625'][0]['mainsnak']['datavalue']['value']
            latitude = str(location['latitude'])
            longitude = str(location['longitude'])
            wikidata_entry['lat_lng'] = ', '.join([latitude, longitude])
        if 'P856' in claims:
            links = claims['P856'][0]['mainsnak']['datavalue']['value']
            wikidata_entry['links'] = links
        if 'P2427' in claims:
            grid_id = claims['P2427'][0]['mainsnak']['datavalue']['value']
            wikidata_entry['grid_id'] = grid_id
        if 'P213' in claims:
            isni = claims['P213'][0]['mainsnak']['datavalue']['value']
            wikidata_entry['isni'] = isni
        if 'P3500' in claims:
            ringgold_id = claims['P3500'][0]['mainsnak']['datavalue']['value']
            wikidata_entry['ringgold_id'] = ringgold_id
    except KeyError:
        return None

    return wikidata_entry


def triage(name, ror_id=None):
    org_data = {}
    claims = ['wikidata_id', 'name', 'name_match_ratio', 'labels', 'established', 'city', 'city_geonames_id', 'admin_terr_name', 'admin_terr_geonames_id',
              'country', 'wikipedia_url', 'links', 'lat_lng', 'grid_id', 'isni', 'crossref_funder_id', 'ringgold_id', 'google_scholar_affiliation_usage', 'orcid_affiliation_usage', 'issue_references']
    outfile = getcwd() + '/triage_result.txt'
    org_name = name
    search_results = search_wikidata(org_name)
    if search_results['search'] != []:
        most_similar = find_most_similar_wikidata_id(org_name, search_results)
        org_data = get_wikidata(
            most_similar[0], most_similar[1], most_similar[2])
    ror_matches = ror_search(org_name)
    if ror_matches != []:
        org_data['ror_matches'] = ror_matches
    crossref_funder_id = funder_id_search(org_name)
    if crossref_funder_id is not None:
        org_data['crossref_funder_id'] = crossref_funder_id
    google_scholar_affiliations = google_scholar_search(org_name)
    if google_scholar_affiliations != []:
        org_data['google_scholar_affiliation_usage'] = '; '.join(
            google_scholar_affiliations)
    else:
        print('No google scholar affiliations found')
    orcid_affiliations = orcid_search(org_name)
    if orcid_affiliations != []:
        org_data['orcid_affiliation_usage'] = ' ; '.join(
            orcid_affiliations)
    else:
        print('No orcid affiliations found')
    if ror_id:
        issue_refs = check_existing_issues(org_name, ror_id)
        if issue_refs:
            org_data['issue_references'] = ' ; '.join(
            issue_refs)
    else:
        issue_refs = check_existing_issues(org_name)
        org_data['issue_references'] = None
    if org_data != {}:
        with open(outfile, 'w') as f_out:
            for claim in claims:
                if claim in org_data.keys():
                    entry = claim + ': ' + str(org_data[claim]) + '\n'
                    f_out.write(entry)

            if 'ror_matches' in org_data.keys():
                for match in org_data['ror_matches']:
                    match_line = ', '.join(match) + '\n'
                    f_out.write(match_line)
    else:
        print('No metadata found for', org_name)


if __name__ == '__main__':
    if len(argv) >= 3:
        triage(argv[1],argv[2])
    else:
        triage(argv[1])
