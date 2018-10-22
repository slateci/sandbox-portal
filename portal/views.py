from flask import (abort, flash, redirect, render_template, request,
                   session, url_for)
import requests

try:
    from urllib.parse import urlencode
except ImportError:
    from urllib import urlencode

from globus_sdk import (TransferClient, TransferAPIError,
                        TransferData, RefreshTokenAuthorizer)

from portal import app
from portal.decorators import authenticated
from portal.utils import (load_portal_client, get_portal_tokens,
                          get_safe_redirect)


spawner_endpoint = "http://localhost:18081"

@app.route('/', methods=['GET'])
def home():
    """Home page - play with it if you must!"""
    return render_template('home.jinja2')


@app.route('/signup', methods=['GET'])
def signup():
    """Send the user to Globus Auth with signup=1."""
    return redirect(url_for('authcallback', signup=1))


@app.route('/login', methods=['GET'])
def login():
    """Send the user to Globus Auth."""
    return redirect(url_for('authcallback'))


@app.route('/sandbox', methods=['GET'])
@authenticated
def sandbox():
    """Show sandboxes"""
    endpoint = None
    authtoken = None
    if 'authtoken' in session:
        authtoken = session['authtoken'] 
        app.logger.info("Credential JSON: %s" % authtoken)
    r = requests.get(spawner_endpoint + "/pod_ready/" + session.get('primary_identity'))
    if r.status_code == requests.codes.ok:
        r2 = requests.get(spawner_endpoint + "/service/" + session.get('primary_identity'))
        if r2.status_code == requests.codes.ok:
            s = r2.json()
        else:
            s = "Error getting service IP/port"
        if authtoken is not None:
            if 'endpoint' in s:
                endpoint = "https://" + s['endpoint'] + "?auth=" + authtoken 
    else:
        s = "No running sandboxes"

    return render_template('sandbox.jinja2',status=s,endpoint=endpoint)

@app.route('/sandbox/new', methods=['GET'])
@authenticated
def launch():
    """Launch a new shell"""
    r = requests.put(spawner_endpoint + "/account/" + session.get('primary_identity'))
    if r.status_code == requests.codes.ok:
        s = r.json()
        app.logger.info("Setting credentials to: %s" % s)
        session['authtoken'] = s['auth']
    else:
        s = "Error creating new sandbox!"

    return redirect(url_for('sandbox'))

@app.route('/sandbox/delete', methods=['GET'])
@authenticated
def delete():
    """ Delete a shell """
    r = requests.delete(spawner_endpoint + "/account/" + session.get('primary_identity'))
    return redirect(url_for('sandbox'))

@app.route('/tutorial', methods=['GET'])
@authenticated
def tutorial():
    endpoint=None
    authtoken = None
    if 'authtoken' in session:
        authtoken = session['authtoken'] 
        app.logger.info("Credential JSON: %s" % authtoken)
    r2 = requests.get(spawner_endpoint + "/service/" + session.get('primary_identity'))
    if r2.status_code == requests.codes.ok:
        s = r2.json()
    else:
        s = "Error getting service IP/port"
    if authtoken is not None:
        if 'endpoint' in s:
            endpoint = "https://" + s['endpoint'] + "?auth=" + authtoken 
    return render_template('tutorial.jinja2', endpoint=endpoint)

@app.route('/tutorial_instructions', methods=['GET'])
@authenticated
def tutorial_instructions():
    return render_template('tutorial_instructions.jinja2')

@app.route('/logout', methods=['GET'])
@authenticated
def logout():
    """
    - Revoke the tokens with Globus Auth.
    - Destroy the session state.
    - Redirect the user to the Globus Auth logout page.
    """
    client = load_portal_client()

    # Revoke the tokens with Globus Auth
    for token, token_type in (
            (token_info[ty], ty)
            # get all of the token info dicts
            for token_info in session['tokens'].values()
            # cross product with the set of token types
            for ty in ('access_token', 'refresh_token')
            # only where the relevant token is actually present
            if token_info[ty] is not None):
        client.oauth2_revoke_token(
            token, additional_params={'token_type_hint': token_type})

    # Destroy the session state
    session.clear()

    redirect_uri = url_for('home', _external=True)

    ga_logout_url = []
    ga_logout_url.append(app.config['GLOBUS_AUTH_LOGOUT_URI'])
    ga_logout_url.append('?client={}'.format(app.config['PORTAL_CLIENT_ID']))
    ga_logout_url.append('&redirect_uri={}'.format(redirect_uri))
    ga_logout_url.append('&redirect_name=Globus Sample Data Portal')

    # Redirect the user to the Globus Auth logout page
    return redirect(''.join(ga_logout_url))


@app.route('/profile', methods=['GET', 'POST'])
@authenticated
def profile():
    """User profile information. Assocated with a Globus Auth identity."""
    if request.method == 'GET':
        identity_id = session.get('primary_identity')
        profile = ''

        if profile:
            name, email, institution = profile

            session['name'] = name
            session['email'] = email
            session['institution'] = institution
        else:
            flash(
                'Please complete any missing profile fields and press Save.')

        if request.args.get('next'):
            session['next'] = get_safe_redirect()

        return render_template('profile.jinja2')
    elif request.method == 'POST':
        name = session['name'] = request.form['name']
        email = session['email'] = request.form['email']
        institution = session['institution'] = request.form['institution']

        #database.save_profile(identity_id=session['primary_identity'],
        #                      name=name,
        #                      email=email,
        #                      institution=institution)

        flash('Thank you! Your profile has been successfully updated.')

        if 'next' in session:
            redirect_to = session['next']
            session.pop('next')
        else:
            redirect_to = url_for('profile')

        return redirect(redirect_to)


@app.route('/authcallback', methods=['GET'])
def authcallback():
    """Handles the interaction with Globus Auth."""
    # If we're coming back from Globus Auth in an error state, the error
    # will be in the "error" query string parameter.
    if 'error' in request.args:
        flash("You could not be logged into the portal: " +
              request.args.get('error_description', request.args['error']))
        return redirect(url_for('home'))

    # Set up our Globus Auth/OAuth2 state
    redirect_uri = url_for('authcallback', _external=True)

    client = load_portal_client()
    client.oauth2_start_flow(redirect_uri, refresh_tokens=True)

    # If there's no "code" query string parameter, we're in this route
    # starting a Globus Auth login flow.
    if 'code' not in request.args:
        additional_authorize_params = (
            {'signup': 1} if request.args.get('signup') else {})

        auth_uri = client.oauth2_get_authorize_url(
            additional_params=additional_authorize_params)

        return redirect(auth_uri)
    else:
        # If we do have a "code" param, we're coming back from Globus Auth
        # and can start the process of exchanging an auth code for a token.
        code = request.args.get('code')
        tokens = client.oauth2_exchange_code_for_tokens(code)

        id_token = tokens.decode_id_token(client)
        session.update(
            tokens=tokens.by_resource_server,
            is_authenticated=True,
            name=id_token.get('name', ''),
            email=id_token.get('email', ''),
            institution=id_token.get('institution', ''),
            primary_username=id_token.get('preferred_username'),
            primary_identity=id_token.get('sub'),
        )

        #profile = database.load_profile(session['primary_identity'])

        #if profile:
        #    name, email, institution = profile

        #    session['name'] = name
        #    session['email'] = email
        #    session['institution'] = institution
#        else:
#            return redirect(url_for('profile',
#                            next=url_for('home')))
#
        return redirect(url_for('home'))
