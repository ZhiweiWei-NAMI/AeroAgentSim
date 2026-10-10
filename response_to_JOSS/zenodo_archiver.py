# Source: https://github.com/zenodo/zenodo/issues/1463#issuecomment-1707525389
# Authors: ('prohde', 'dr-joe-wirth')

import requests
import sys

def _submit(user_name:str, repo_name:str, tag:str, access_token:str) -> None:
    """tricks Zenodo into archiving a pre-existing release

    Args:
        user_name (str): github username or organization name
        repo_name (str): github repository name
        tag (str): the desired tag to archive
        access_token (str): the access token from webhook page in repo settings
    """
    # constant
    HEADERS = {"Accept": "application/vnd.github.v3+json"}

    # build the repo string
    repo = "/".join((user_name, repo_name))
    
    # get the repo response and the release response for the repo
    print(f"Fetching data for repository: {repo}")
    repo_response = requests.get(f"https://api.github.com/repos/{repo}", headers=HEADERS)
    release_response = requests.get(f"https://api.github.com/repos/{repo}/releases", headers=HEADERS)
    
    # Check if responses were successful
    if repo_response.status_code != 200:
        print(f"Error fetching repository data: {repo_response.status_code} - {repo_response.text}")
        return
    if release_response.status_code != 200:
        print(f"Error fetching releases data: {release_response.status_code} - {release_response.text}")
        return

    # get the data for the desired release
    try:
        desired_release = [x for x in release_response.json() if x['tag_name'] == tag].pop()
        print(f"Found release data for tag: {tag}")
    except IndexError:
        print(f"Error: Could not find a release with the tag '{tag}' in the repository.")
        return
        
    # build the payload for the desired release
    payload = {"action": "published", "release": desired_release, "repository": repo_response.json()}
    
    # build the target Zenodo URL
    zenodo_url = f"https://zenodo.org/api/hooks/receivers/github/events/?access_token={access_token}"
    
    # submit the payload to zenodo's api
    print("Sending request to Zenodo...")
    submit_response = requests.post(zenodo_url, json=payload)
    
    # print the response
    print("Zenodo's response:")
    print(submit_response)
    print(submit_response.text)

def _main() -> None:
    """main runner function"""
    if len(sys.argv) != 5:
        print("Usage: python3 zenodo_archiver.py <user_name> <repo_name> <tag> <access_token>")
        return
        
    # parse command line arguments
    user  = sys.argv[1]
    repo  = sys.argv[2]
    tag   = sys.argv[3]
    token = sys.argv[4]
    
    # submit to zenodo
    _submit(user, repo, tag, token)

# entrypoint
if __name__ == "__main__":
    _main()