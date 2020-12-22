# -*- coding: utf-8 -*-

# This sample demonstrates handling intents from an Alexa skill using the Alexa Skills Kit SDK for Python.
# Please visit https://alexa.design/cookbook for additional examples on implementing slots, dialog management,
# session persistence, api calls, and more.
# This sample is built using the handler classes approach in skill builder.
import boto3
import json
import logging
import os
import requests
import threading
import uuid
import random
import ask_sdk_core.utils as ask_utils

from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler
from ask_sdk_core.dispatch_components import AbstractExceptionHandler
from ask_sdk_core.handler_input import HandlerInput

from ask_sdk_model import Response
from ask_sdk_model.dialog import ElicitSlotDirective, DynamicEntitiesDirective, DelegateDirective
from ask_sdk_model.dialog_state import DialogState
from ask_sdk_model.er.dynamic import Entity, EntityValueAndSynonyms, EntityListItem, UpdateBehavior
from ask_sdk_model.slu.entityresolution import StatusCode

from ask_sdk_model.interfaces.connections import SendRequestDirective
from ask_sdk_model.interfaces.amazonpay.request.setup_amazon_pay_request import SetupAmazonPayRequest
from ask_sdk_model.interfaces.amazonpay.model.request.billing_agreement_attributes import BillingAgreementAttributes
from ask_sdk_model.interfaces.amazonpay.model.request.seller_billing_agreement_attributes import SellerBillingAgreementAttributes
from ask_sdk_model.interfaces.amazonpay.model.request.billing_agreement_type import BillingAgreementType
from ask_sdk_model.interfaces.amazonpay.response.setup_amazon_pay_result import SetupAmazonPayResult
from ask_sdk_model.interfaces.amazonpay.request.charge_amazon_pay_request import ChargeAmazonPayRequest
from ask_sdk_model.interfaces.amazonpay.model.request.authorize_attributes import AuthorizeAttributes
from ask_sdk_model.interfaces.amazonpay.model.request.seller_order_attributes import SellerOrderAttributes
from ask_sdk_model.ui import AskForPermissionsConsentCard
from ask_sdk_model.interfaces.amazonpay.model.request.price import Price
from ask_sdk_model.interfaces.amazonpay.model.request.payment_action import PaymentAction
from dotenv import load_dotenv

from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

load_dotenv()

AWS_REGION = os.environ.get('AWS_REGION')

ORDER_SERVICE_URL = os.environ.get('ORDER_SERVICE_URL')
PRODUCT_SERVICE_URL = os.environ.get('PRODUCT_SERVICE_URL')
RECOMMENDATIONS_SERVICE_URL = os.environ.get('RECOMMENDATIONS_SERVICE_URL')
LOCATION_SERVICE_URL = os.environ.get('LOCATION_SERVICE_URL')

PINPOINT_APP_ID = os.environ.get('PINPOINT_APP_ID')
COGNITO_DOMAIN = os.environ.get('COGNITO_DOMAIN')
LOCATION_PLACE_INDEX_NAME = os.environ.get('LOCATION_PLACE_INDEX_NAME')

ASSUME_ROLE_ARN = os.environ.get('ASSUME_ROLE_ARN')

PRODUCT_CATEGORIES = ['food service', 'salty snacks', 'hot drinks', 'cold dispensed']

AMAZON_PAY_MERCHANT_ID = os.environ.get('AMAZON_PAY_MERCHANT_ID', '').strip()
SANDBOX_CUSTOMER_EMAIL = os.environ.get('SANDBOX_CUSTOMER_EMAIL', '').strip()
FORCE_ASK_PAY_PERMISSIONS = False

sts_client = boto3.client('sts')
assumed_role_object=sts_client.assume_role(
    RoleArn=ASSUME_ROLE_ARN,
    RoleSessionName="AssumeRoleSession"
)
credentials = assumed_role_object['Credentials']

pinpoint = boto3.client(
    'pinpoint', 
    aws_access_key_id=credentials['AccessKeyId'],
    aws_secret_access_key=credentials['SecretAccessKey'],
    aws_session_token=credentials['SessionToken'],
    region_name=AWS_REGION
)

location = boto3.client(
    'location',
    aws_access_key_id=credentials['AccessKeyId'],
    aws_secret_access_key=credentials['SecretAccessKey'],
    aws_session_token=credentials['SessionToken'],
    region_name=AWS_REGION
)


def get_cognito_user_details(handler_input):
    session_attr = handler_input.attributes_manager.session_attributes
    try:
        access_token = handler_input.request_envelope.context.system.user.access_token
        url = f"{COGNITO_DOMAIN}/oauth2/userInfo"
        logger.info(f"Obtaining user info from {url}")
        req = Request(url)
        req.add_header('Authorization', f'Bearer {access_token}')
        user_details = json.loads(urlopen(req).read().decode('utf-8'))
        logger.info(f"Got user info from Cognito: {user_details}")
    except Exception as e:
        # Here, we allow for easy testing without having to do the authentication of Alexa with Cognito
        # This is important if you want to test Retail Demo Store on the web because only the mobile app
        # allows you to grab the authentication token from another provider
        # If there is a tester email set up with SANDBOX_CUSTOMER_EMAIL in .env
        # we use that for emails, otherwise you will unfortunately not
        # receive any emails.
        user_details = {
            'username': 'daemon',
            'custom:profile_user_id': '0',
            'custom:profile_first_name': 'Testy',
            'custom:profile_last_name': 'McTest',
            'email': SANDBOX_CUSTOMER_EMAIL
        }
        logger.info(f"Default user details retrieved: {user_details} - exception: {e}")

    session_attr['CognitoUser'] = user_details
    return user_details


def send_email(to_email, subject, html_content, text_content):
    """
    Send a default email to the address. Pull pinpoint app ID and from address from env.
    More information about this service:
    https://docs.aws.amazon.com/pinpoint/latest/developerguide/send-messages-email.html
    Character set is UTF-8.
    Args:
        to_email: Email to send to
        subject: Subject of email
        html_content: HTML version of email content
        text_content: Plain text version of email content

    Returns:

    """

    pinpoint_app_id = PINPOINT_APP_ID
    response = pinpoint.send_messages(
        ApplicationId=pinpoint_app_id,
        MessageRequest={
            'Addresses': {
                to_email: {
                    'ChannelType': 'EMAIL'
                }
            },
            'MessageConfiguration': {
                'EmailMessage': {
                    'SimpleEmail': {
                        'Subject': {
                            'Charset': "UTF-8",
                            'Data': subject
                        },
                        'HtmlPart': {
                            'Charset': "UTF-8",
                            'Data': html_content
                        },
                        'TextPart': {
                            'Charset': "UTF-8",
                            'Data': text_content
                        }
                    }
                }
            }
        }
    )
    logger.info(f'Message sent to {to_email} and response: {response}')


def send_order_confirm_email(handler_input, orders, add_images=True):
    """
    Take info about a waiting order and send it to customer saying ready for pickup as email
    Args:
        handler_input: Input to the Lambda handler. Used to access products in session state.
        to_email: Where to send the email to
        orders: Orders as obtained from get_orders_with_details()
    Returns:
        Nothing but sends an email.
    """

    session_attr = handler_input.attributes_manager.session_attributes

    user_email = get_cognito_user_details(handler_input)['email']

    order_ids = ', '.join(['#' + str(order['id']) for order in orders])

    # Specify content:
    subject = "Your order has been received!"
    heading = "Welcome,"
    subheading = f"Your order has been paid for with Amazon Pay."
    intro_text = f"""We will meet you at your pump with the following order ({order_ids}):"""
    html_intro_text = intro_text.replace('\n', '</p><p>')

    # Build the order list in text and HTML at the same time.
    html_orders = "<ul>"
    text_orders = ""
    for order in orders:
        order_name = f"Order #{order['id']}"
        html_orders += f"\n  <li>{order_name}:<ul>"
        text_orders += f'\n{order_name}:'
        for item in order['items']:
            if 'details' in item:
                img_url = item["details"]["image_url"]
                url = item["details"]["url"]
                name = item["details"]["name"]
            else:
                product = session_attr['Products'][item['product_id']]
                img_url = product.get("image", "")
                url = product.get("url", "")
                name = product.get("name", "Retail Demo Store Product")
            if add_images and img_url and len(img_url) > 0:
                img_tag = f'<img src="{img_url}" width="100px">'
            else:
                img_tag = ''
            html_orders += F'\n    <li><a href="{url}">{name}</a> - ${item["price"]:0.2f}<br/><a href="{url}">{img_tag}</a></br></a></li>'
            text_orders += f'\n  - {name} - ${item["price"]:0.2f} {url}'
        html_orders += "\n  </ul></li>"
    html_orders += "\n</ul>"

    # Build HTML message
    html = f"""
    <head></head>
    <body>
        <h1>{heading}</h1>
        <h2>{subheading}</h2>
        <p>{html_intro_text}
        {html_orders}
        <p><a href="{os.environ.get('WebURL','')}">Thank you for shopping!</a></p>
    </body>
    """

    # Build text message
    text = f"""
{heading}
{subheading}
{intro_text}
{text_orders}
Thank you for shopping!
{os.environ.get('WebURL','')}
    """

    logger.debug(f"Contents of email to {user_email} html: \n{html}")
    logger.debug(f"Contents of email to {user_email} text: \n{text}")
    send_email(user_email, subject, html, text)


def fetch_product_slot_directive(handler_input):
    products = []
    for category in PRODUCT_CATEGORIES:
        category_products = json.loads(urlopen(f'{PRODUCT_SERVICE_URL}/products/category/{category.replace(" ", "%20")}').read().decode('utf-8'))
        products += category_products

    session_attr = handler_input.attributes_manager.session_attributes
    if 'Products' not in session_attr:
        session_attr['Products'] = {}
        for product in products:
            session_attr['Products'][product['id']] = {'name': product['name'], 'price': product['price'], 'image': product['image'], 'url': product['url']}

    entity_list_values = []
    for product in products:
        value_and_synonyms = EntityValueAndSynonyms(value=product['name'], synonyms=product['aliases'])
        entity_list_values.append(Entity(id=product['id'], name=value_and_synonyms))

    return EntityListItem(name="Product", values=entity_list_values)


def get_matched_product_id(handler_input):
    """Retrieves the product ID when using the ProductName slot"""
    resolutions_per_authority = handler_input.request_envelope.request.intent.slots['ProductName'].resolutions.resolutions_per_authority
    for resolution in resolutions_per_authority:
        if resolution.status.code == StatusCode.ER_SUCCESS_MATCH:
            return resolution.values[0].value.id


def get_recommended_product(handler_input, product_id):
    """Retrieves the recommended ID when using the ProductName slot"""
    session_attr = handler_input.attributes_manager.session_attributes
    
    if 'RecommendedProducts' not in session_attr:
        session_attr['RecommendedProducts'] = {}

    if product_id not in session_attr['RecommendedProducts']:
        logger.info(f'{RECOMMENDATIONS_SERVICE_URL}/related?currentItemID={product_id}&numResults=5&feature=alexa&userID=5999&filter=cstore')
        recommended_products = json.loads(urlopen(f'{RECOMMENDATIONS_SERVICE_URL}/related?currentItemID={product_id}&numResults=5&feature=alexa&userID=1&filter=cstore').read().decode('utf-8'))
        if len(recommended_products)>0:
            recommended_product = recommended_products[0]['product']
            session_attr['RecommendedProducts'][product_id] = {'id': recommended_product['id'], 'name': recommended_product['name'], 'price': recommended_product['price']}
        else:
            logger.error("Could not retrieve a recommendation.")
            all_product_ids = list(session_attr['Products'].keys())
            random_product_id = all_product_ids[random.randrange(0,len(all_product_ids))]
            random_product = session_attr['Products'][random_product_id]
            session_attr['RecommendedProducts'][product_id] = {'id': random_product_id,
                                                               'name': random_product['name'],
                                                               'price': random_product['price']}

    return session_attr['RecommendedProducts'][product_id]


def get_product_by_id(handler_input, product_id):
    session_attr = handler_input.attributes_manager.session_attributes
    return session_attr['Products'][product_id]


def submit_order(handler_input):
    session_attr = handler_input.attributes_manager.session_attributes

    user_details = get_cognito_user_details(handler_input)
    if user_details['custom:profile_user_id'].isnumeric():
        username = f"user{user_details['custom:profile_user_id']}"
        first_name = user_details['custom:profile_first_name']
        last_name = user_details['custom:profile_last_name']
    else:
        username = user_details['username']
        first_name = user_details['username']
        last_name = ""
    
    order = {
        "items": [],
        "total": get_basket_total(handler_input),
        "delivery_type": 'COLLECTION',
        "username": username,
        "billing_address": {
            "first_name": first_name,
            "last_name": last_name
        },
        "channel": "alexa"
    }
    
    for item_id, basket_item in session_attr['Basket'].items():
        order_item = {
            'product_id': item_id,
            'quantity': basket_item['quantity'],
            'price': get_product_by_id(handler_input, item_id)['price']
        }
        order['items'].append(order_item)
    
    logger.info(f"Submitting order: {order}")
    req = Request(f'{ORDER_SERVICE_URL}/orders', method='POST', data=json.dumps(order).encode('utf-8'))
    order_response = json.loads(urlopen(req).read().decode('utf-8'))
    logger.info(f"Order response: {order_response}")
    
    return order_response


def distance_km(point1, point2):
   """Convert from degrees - approximate"""
   return ((point1[0] - point2[0])**2 + (point1[1] - point2[1])**2)**0.5 * 111


def location_search_cstore():
    # Get customer faked location (could read it from device but we might be demo-ing from the web where location
    # is not available).
    cstore_route = json.loads(urlopen(f'{LOCATION_SERVICE_URL}/cstore_route').read().decode('utf-8'))
    customer_position = cstore_route['features'][0]['geometry']['coordinates'][0]

    # Do the search for nearby Exxon
    response = location.search_place_index_for_text(IndexName=LOCATION_PLACE_INDEX_NAME,
                                                    Text="Exxon",
                                                    BiasPosition=customer_position)

    # Grab address and make it sound nice - could be a lot more sophisticated here
    address = response['Results'][0]['Place']
    spoken_address = address['AddressNumber'] + " " + address['Street']
    store_position = address['Geometry']['Point']

    # How far away is that?
    shop_dist_km = distance_km(store_position, customer_position)
    shop_dist_miles = 0.6214 * shop_dist_km

    logger.info(f"Closest Exxon to {customer_position} is at {store_position}, "
                f"with spoken address {spoken_address} and distance {shop_dist_km:0.0f}km"
                f" ({shop_dist_miles:0.0f} miles)")

    # Override for the demo for consistency
    spoken_address = "640 Elk St."
    shop_dist_miles = 3

    return spoken_address, shop_dist_miles


def get_basket_id(handler_input):
    session_attr = handler_input.attributes_manager.session_attributes
    return session_attr['BasketId']


def get_basket_total(handler_input):
    total = 0
    session_attr = handler_input.attributes_manager.session_attributes
    
    if 'Basket' not in session_attr:
        return total
        
    for item_id, basket_item in session_attr['Basket'].items():
        total += session_attr['Products'][item_id]['price'] * basket_item['quantity']
        
    return total


def add_product_to_basket(handler_input, prod_id):
    session_attr = handler_input.attributes_manager.session_attributes
    
    if 'Basket' not in session_attr:
        session_attr['Basket'] = {}
        session_attr['BasketId'] = str(uuid.uuid4())[:32]
    
    if prod_id not in session_attr['Basket']:
        session_attr['Basket'][prod_id] = {'quantity': 1}
    else: 
        session_attr['Basket'][prod_id]['quantity'] += 1


def set_question_asked(handler_input, question=''):
    """
    Sets an identifier for the question last asked to allow for the handling of yes/no questions outside a DialogState.
    This identifier is persisted in the Alexa session_attributes and should be removed upon handling the response
    to avoid unexpected consequences in the handling following questions.
    
    The expected flow is as follows:
        - Yes/no question is asked
        - The identifier for that question is persisted to session_attributes
        - The yes/no question is answered and handled by the AMAZON.YesIntent/AMAZON.NoIntent
        - The combination of Yes/NoIntent and the previous quesiton asked can be used to determine
          how the response should be handled.
    
    Parameters:
        - handler_input (dict): The handler_input dict used to call the intent handler. 
        - question (str):       The identifier for the question asked.
        
    Returns: 
        None
    """
    
    handler_input.attributes_manager.session_attributes['PreviousQuestion'] = question
    

def get_question_asked(handler_input):
    """
    Gets an identifier for the previous question asked to allow for the handling of yes/no questions outside of a
    DialogState. This identifier is persisted in the Alexa session_attributes and should be removed upon handling the
    response to avoid unexpected consequences in the handling following questions.
    
    Parameters:
        - handler_input (dict): The handler_input dict used to call the intent handler. 
        
    Returns: 
        String: The string identifier of the last question asked. 
    """
    
    return handler_input.attributes_manager.session_attributes['PreviousQuestion']


class LaunchRequestHandler(AbstractRequestHandler):
    """Handler for Skill Launch."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("Calling LaunchRequestHandler")
        speak_output = "Welcome to the C-Store Demo. Ask where your nearest Exxon is to start an order there."

        return (
            handler_input.response_builder
                         .speak(speak_output)
                         .ask(speak_output)
                         .response
        )


class FindStoreIntentHandler(AbstractRequestHandler):
    """Handler for Find Store Intent. Grab nearest Exxon using Amazon Location Service.
    Meanwhile, fill in the list of available products (e.g. these could depend on the store chosen)
    using `fetch_product_slot_directive()`"""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("FindStoreIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("Calling FindStoreIntentHandler")

        # The address and distance will look like this:
        spoken_address, shop_dist_miles = location_search_cstore()
        speak_output = f"There is an Exxon {shop_dist_miles:0.0f} miles away at {spoken_address} " \
                       "Would you like to pre-order items to collect when you arrive?"
        set_question_asked(handler_input, 'START_PREORDER')
        product_slot_directive = DynamicEntitiesDirective(update_behavior=UpdateBehavior.REPLACE,
                                                          types=[fetch_product_slot_directive(handler_input)])

        return (
            handler_input.response_builder
                         .speak(speak_output)
                         .ask("Would you like to pre-order items to collect when you arrive?")
                         .add_directive(product_slot_directive)
                         .response
        )


class OrderProductIntentHandler(AbstractRequestHandler):
    """Handler for Order Product Intent. Fill in ordered product and recommended product, add ordered
    product to basket, tell the user we've done this and offer recommendation. Elicit the recommendation Yes/No
    response."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (
            ask_utils.is_intent_name("OrderProductIntent")(handler_input) 
            and ask_utils.get_dialog_state(handler_input) == DialogState.STARTED
        )

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("Calling OrderProductIntentHandler")
        product_name = ask_utils.get_slot_value(handler_input, 'ProductName')
        
        product_id = get_matched_product_id(handler_input)
        recommended_product = get_recommended_product(handler_input, product_id)
        add_product_to_basket(handler_input, product_id)
        product = get_product_by_id(handler_input, product_id)
        
        speak_output = f"Sure. Ordering {product_name} for ${product['price']}. " \
                       f"Would you like to add {recommended_product['name']} to your basket too?"
        recommended_product_directive = ElicitSlotDirective(slot_to_elicit='AddRecommendedProduct')

        return (
            handler_input.response_builder
                .speak(speak_output)
                .add_directive(recommended_product_directive)
                .response
        )


class AddRecommendedProductHandler(AbstractRequestHandler):
    """Handler for recommended product within OrderProduct dialog.
    If the user wants the recommended product, add it to basket.
    If they say they don't want it, do not add it. Then loop by setting the question asked state var to ORDER_MORE"""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (
            ask_utils.is_intent_name("OrderProductIntent")(handler_input)
            and ask_utils.get_dialog_state(handler_input) == DialogState.IN_PROGRESS
        )

    def add_recommended_product(self, handler_input):
        product_id = get_matched_product_id(handler_input)
        recommended_product = get_recommended_product(handler_input, product_id)
        add_product_to_basket(handler_input, recommended_product['id'])
    
    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info(f"Calling AddRecommendedProductHandler: {json.dumps(handler_input.request_envelope.to_dict(), default=str, indent=2)}")
        
        # add_recommended_product will either be "0" (no) or "1" (yes)
        should_add_recommended_product = ask_utils.get_slot(handler_input, 'AddRecommendedProduct').resolutions.resolutions_per_authority[0].values[0].value.id
        if should_add_recommended_product == "1":
            self.add_recommended_product(handler_input)
            recommended_product = get_recommended_product(handler_input, get_matched_product_id(handler_input))
            speak_output = f"Adding {recommended_product['name']} for ${recommended_product['price']}!"
        else: 
            speak_output = "Sure."
        
        speak_output += " Would you like to order anything else?"
        set_question_asked(handler_input, 'ORDER_MORE')
        
        return (
            handler_input.response_builder
                .speak(speak_output)
                .set_should_end_session(False)
                .response
        )


class ToCheckoutHandler(AbstractRequestHandler):
    """User responds "No" to whether to order more.
    Delegate to the Checkout intent."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (
                ask_utils.is_intent_name("AMAZON.NoIntent")(handler_input) and get_question_asked(
                                                                                    handler_input) == 'ORDER_MORE'
        )

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("Calling ToCheckoutHandler")

        checkout_delegate_directive = DelegateDirective(updated_intent={'name': 'CheckoutIntent'})

        return (
            handler_input.response_builder
                .add_directive(checkout_delegate_directive)
                .response
        )


class CheckoutIntentHandler(AbstractRequestHandler):
    """Handler for the Checkout Intent. Set up Amazon Pay. This intent be accessed at any time - e.g. you can
    shortcut a recommendation suggestion by just saying "checkout".
    Note that we could have opted to set up Amazon Pay at a different part of the flow, but we wait till the
    first checkout. After Amazon Pay is given permissions it will keep these permissions. This is all taken care of by Alexa."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (
            ask_utils.is_intent_name("CheckoutIntent")(handler_input)
        )

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info(f"Calling CheckoutIntentHandler {json.dumps(handler_input.request_envelope.to_dict(), default=str, indent=2)}")

        basket_total = get_basket_total(handler_input)


        if len(AMAZON_PAY_MERCHANT_ID)==0:

            speak_output = f"Your total is ${basket_total}. "
            # This happens if AMAZON_PAY_MERCHANT_ID not set up in .env file
            speak_output += "This demo has no merchant set up so we'll just finish up now. Thanks for playing!"

            return (
                handler_input.response_builder
                             .speak(speak_output)
                             .set_should_end_session(True)
                             .response
            )

        else:

            """
            Below is an example of further attributes you could specify, to override the default.

            user_details = get_cognito_user_details(handler_input)
            basket_id = get_basket_id(handler_input)

            seller_billing_agreement_attributes=SellerBillingAgreementAttributes(
                version="2",
                seller_billing_agreement_id=user_details['username'] + '-' + basket_id,
                store_name="C store demo",
                custom_information="A demonstration of Alexa Pay integration"
            )

            billing_agreement_type = BillingAgreementType("CustomerInitiatedTransaction") # BillingAgreementType("MerchantInitiatedTransaction") #

            billing_agreement_attributes = BillingAgreementAttributes(
                version="2",
                billing_agreement_type=billing_agreement_type, # EU/UK only
                seller_note="C store demo payment sandbox only",
                seller_billing_agreement_attributes=seller_billing_agreement_attributes
            )
            """

            # Let us save our session as a token for Pay, because by handing over to Pay, the Alexa session has ended.
            # Alternatively, you could save these in a backend DB.
            correlation_token = json.dumps(handler_input.attributes_manager.session_attributes)

            pay_request = SetupAmazonPayRequest(
                version="2",
                seller_id=AMAZON_PAY_MERCHANT_ID,
                country_of_establishment="US",
                ledger_currency="USD",
                checkout_language="en-US",
                sandbox_mode=True,
                sandbox_customer_email_id=SANDBOX_CUSTOMER_EMAIL,
                # extra params could be added here: billing_agreement_attributes=billing_agreement_attributes,
                need_amazon_shipping_address=False)

            pay_setup_directive = SendRequestDirective(
                name='Setup',
                token=correlation_token,
                payload=pay_request
            )

            logger.info(f"SendRequestDirective: {pay_setup_directive}")

            response_builder = handler_input.response_builder
            # We may need to ask the user for permissions to use Amazon Pay to make payments
            # Alexa may do this automatically but it may not.
            autopay = 'payments:autopay_consent'
            permissions = handler_input.request_envelope.context.system.user.permissions
            scopes = None if permissions is None else permissions.scopes

            logger.info(f"Permissions: scopes: {scopes} status: {scopes[autopay].status}")
            logger.info(f"Status: name: {scopes[autopay].status.name} value: {scopes[autopay].status.value}")

            if (scopes is None or autopay not in scopes or scopes[autopay].status.value != "GRANTED") \
                    and FORCE_ASK_PAY_PERMISSIONS:
                response_builder = response_builder.speak("Please give permission to use Amazon Pay to check out.")
                response_builder = response_builder.set_card(
                    AskForPermissionsConsentCard(permissions=[autopay]))
                # redelegate = DelegateDirective(updated_intent={'name': 'CheckoutIntent'})
                # response_builder = response_builder.add_directive(redelegate)
            else:
                response_builder = response_builder.speak("Thank you!")
                response_builder = response_builder.add_directive(pay_setup_directive)

            return response_builder.response


class AmazonPaySetupResponseHandler(AbstractRequestHandler):
    """Handler for When Amazon Pay responds to our attempt to set up Amazon Pay,
    after the user has started the checkout process. We'll use this as our cue to charge the user."""
    def can_handle(self, handler_input):
        connection_response = ask_utils.is_request_type("Connections.Response")(handler_input)
        if connection_response:
            envelope = handler_input.request_envelope
            logger.info(f"We have a connection response: {envelope}")
            return (envelope.request.name == "Setup")
        return False

    def handle(self, handler_input):

        logger.info(f"Calling AmazonPaySetupResponseHandler with input "
                    f"{json.dumps(handler_input.request_envelope.to_dict(), default=str, indent=2)}")

        action_response_payload = handler_input.request_envelope.request.payload
        action_response_status_code = handler_input.request_envelope.request.status.code
        correlation_token = handler_input.request_envelope.request.token

        if int(action_response_status_code) != 200:

            message = handler_input.request_envelope.request.status.message
            logstr = f"Not an OK return status from Amazon Pay Setup: {action_response_status_code} " \
                     f"with payload {action_response_payload} and message {message} "
            speak_output = f"There was a problem with Amazon Pay Setup: {message} "
            try:
                speak_output += action_response_payload.error_message
                logstr += action_response_payload.error_message
            except:
                pass
            logger.error(logstr)
            return (
                handler_input.response_builder
                    .speak(speak_output)
                    .set_should_end_session(True)
                    .response
            )

        if len(AMAZON_PAY_MERCHANT_ID)==0:

            speak_output = "This demo has no merchant setup! We hope you had fun."

            return (
                handler_input.response_builder
                    .speak(speak_output)
                    .set_should_end_session(True)
                    .response
            )

        else:
            SetupAmazonPayResult()
            billing_agreement_details = action_response_payload['billingAgreementDetails']
            billing_agreement_id = billing_agreement_details['billingAgreementId']

            # Because we handed over to Amazon Pay we lost our session and with it the attributes, but Pay allows
            # us to send a token, which we used to save these. Alternatively, we could have saved them in the backend,
            # keyed by, for example, seller_billing_agreement_id (sellerBillingAgreementId).
            handler_input.attributes_manager.session_attributes = json.loads(correlation_token)

            basket_total = get_basket_total(handler_input)
            basket_id = get_basket_id(handler_input)

            """If we wanted to we could add more information to our charge:""
             seller_order_attributes = SellerOrderAttributes(
                 version="2",
                 seller_order_id=user_details['username'] + '-' + get_basket_id(handler_input),
                 store_name="Retail Demo Store",
                 custom_information="A Demo Transaction For Retail Demo Store",
                 seller_note="Congratulations on your purchase via Alexa and Amazon Pay at the C-Store demo!"
             )
            """

            authorization_amount = Price(
                version="2",
                amount=f"{basket_total:0.2f}",
                currency_code="USD"
            )

            authorize_attributes = AuthorizeAttributes(
                version="2",
                authorization_reference_id=basket_id,
                authorization_amount=authorization_amount,
                seller_authorization_note="Retail Demo Store Sandbox Transaction",
            )

            payment_action = PaymentAction('AuthorizeAndCapture')

            charge_request = ChargeAmazonPayRequest(
                version="2",
                seller_id=AMAZON_PAY_MERCHANT_ID,
                billing_agreement_id=billing_agreement_id,
                payment_action=payment_action,
                authorize_attributes=authorize_attributes,
                # This is where we would add extra information: seller_order_attributes=seller_order_attributes
            )

            charge_directive = SendRequestDirective(
                name='Charge',
                token=correlation_token,
                payload=charge_request
            )

            return (
                handler_input.response_builder
                    .add_directive(charge_directive)
                    .set_should_end_session(True)
                    .response
            )


class AmazonPayChargeResponseHandler(AbstractRequestHandler):
    """Handler for When Amazon Pay responds to our attempt to charge the customer."""
    def can_handle(self, handler_input):
        connection_response = ask_utils.is_request_type("Connections.Response")(handler_input)
        if connection_response:
            envelope = handler_input.request_envelope
            return (envelope.request.name == "Charge")
        return False

    def handle(self, handler_input):
        logger.info(f"Calling AmazonPayChargeResponseHandler with input "
                    f"{json.dumps(handler_input.request_envelope.to_dict(), default=str, indent=2)}")

        request = handler_input.request_envelope.request
        action_response_status_code = request.status.code

        if int(action_response_status_code) != 200:

            message = request.status.message
            logger.error(f"Not an OK return status from Amazon Pay Charge: {action_response_status_code} "
                         f"and message {message}")
            return (
                handler_input.response_builder
                    .speak(f"There was a problem with Amazon Pay Charge: {message}")
                    .set_should_end_session(True)
                    .response
            )

        # see above - we have kept our session attributes in a token
        correlation_token = handler_input.request_envelope.request.token
        handler_input.attributes_manager.session_attributes = json.loads(correlation_token)

        order_response = submit_order(handler_input)
        send_order_confirm_email(handler_input, [order_response], False)

        speak_output = f"Your order will be ready when you arrive."
        return (
            handler_input.response_builder
                .speak(speak_output)
                .set_should_end_session(True)
                .response
        )


class OrderProductHandler(AbstractRequestHandler):
    """We have asked "would you like to order something" so if the answer is yes, we delegate to the
    OrderProductIntent."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (
            ask_utils.is_intent_name("AMAZON.YesIntent")(handler_input) 
            and (get_question_asked(handler_input) in ['START_PREORDER', 'ORDER_MORE'])
        )

    def handle(self, handler_input):
        logger.info("Calling OrderMoreHandler")
        
        set_question_asked(handler_input)

        order_product_delegate_directive = DelegateDirective(updated_intent={'name': 'OrderProductIntent'})
        
        return (
            handler_input.response_builder
                .add_directive(order_product_delegate_directive)
                .response
        )


class NoProductOrderHandler(AbstractRequestHandler):
    """We have asked "would you like to order something" so if the answer is no, we bid farewell."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (
            ask_utils.is_intent_name("AMAZON.NoIntent")(handler_input) 
            and (get_question_asked(handler_input) == 'START_PREORDER')
        )
        
    def handle(self, handler_input):
        logger.info("Calling NoProductOrderHandler")
        
        speak_output = "Have a safe trip!"
        
        return (
            handler_input.response_builder
                .speak(speak_output)
                .response
        )


class HelpIntentHandler(AbstractRequestHandler):
    """Handler for Help Intent."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("Calling HelpIntentHandler")
        
        speak_output = "You can say hello to me! How can I help?"

        return (
            handler_input.response_builder
                .speak(speak_output)
                .ask(speak_output)
                .response
        )


class CancelOrStopIntentHandler(AbstractRequestHandler):
    """Single handler for Cancel and Stop Intent."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (ask_utils.is_intent_name("AMAZON.CancelIntent")(handler_input) or
                ask_utils.is_intent_name("AMAZON.StopIntent")(handler_input))

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("Calling CancelOrStopIntentHandler")
        speak_output = "Goodbye!"

        return (
            handler_input.response_builder
                .speak(speak_output)
                .response
        )


class SessionEndedRequestHandler(AbstractRequestHandler):
    """Handler for Session End."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("Calling SessionEndedRequestHandler")

        # Any cleanup logic goes here.

        return handler_input.response_builder.response


class IntentReflectorHandler(AbstractRequestHandler):
    """The intent reflector is used for interaction model testing and debugging.
    It will simply repeat the intent the user said. You can create custom handlers
    for your intents by defining them above, then also adding them to the request
    handler chain below.
    """
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_request_type("IntentRequest")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        intent_name = ask_utils.get_intent_name(handler_input)
        speak_output = "You just triggered " + intent_name + "."

        return (
            handler_input.response_builder
                .speak(speak_output)
                # .ask("add a reprompt if you want to keep the session open for the user to respond")
                .response
        )


class CatchAllExceptionHandler(AbstractExceptionHandler):
    """Generic error handling to capture any syntax or routing errors. If you receive an error
    stating the request handler chain is not found, you have not implemented a handler for
    the intent being invoked or included it in the skill builder below.
    """
    def can_handle(self, handler_input, exception):
        # type: (HandlerInput, Exception) -> bool
        return True

    def handle(self, handler_input, exception):
        # type: (HandlerInput, Exception) -> Response
        logger.error(exception, exc_info=True)

        speak_output = "Sorry, I had trouble doing what you asked. Please try again."

        return (
            handler_input.response_builder
                .speak(speak_output)
                .ask(speak_output)
                .response
        )


sb = SkillBuilder()

sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(FindStoreIntentHandler())
sb.add_request_handler(OrderProductIntentHandler())
sb.add_request_handler(AddRecommendedProductHandler())
sb.add_request_handler(CheckoutIntentHandler())
sb.add_request_handler(OrderProductHandler())
sb.add_request_handler(NoProductOrderHandler())
sb.add_request_handler(ToCheckoutHandler())
sb.add_request_handler(AmazonPaySetupResponseHandler())
sb.add_request_handler(AmazonPayChargeResponseHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_request_handler(IntentReflectorHandler()) # make sure IntentReflectorHandler is last so it doesn't override your custom intent handlers

sb.add_exception_handler(CatchAllExceptionHandler())

lambda_handler = sb.lambda_handler()
