"""GraphQL documents for the handful of read-only operations R2 Waiting Room uses.

Core documents are the ones community clients have used since 2023 (see
the-mace/rivian-python-api, MIT). Extended documents add the fields the Rivian
garage web app shows (configurationStatus, invitedToSelfConfirm, the 13-step
consumerStatuses block). If Rivian's schema rejects an extended document the
client falls back to the core one and records the error, so an unknown field
never takes the whole poll down.
"""

GATEWAY = "https://rivian.com/api/gql/gateway/graphql"
ORDERS = "https://rivian.com/api/gql/orders/graphql"
T2D = "https://rivian.com/api/gql/t2d/graphql"

CREATE_CSRF = {
    "operationName": "CreateCSRFToken",
    "query": "mutation CreateCSRFToken { createCsrfToken { __typename csrfToken appSessionToken } }",
    "variables": None,
}

LOGIN = {
    "operationName": "Login",
    "query": (
        "mutation Login($email: String!, $password: String!) { login(email: $email, password: $password) { __typename "
        "... on MobileLoginResponse { __typename accessToken refreshToken userSessionToken } "
        "... on MobileMFALoginResponse { __typename otpToken } } }"
    ),
}

LOGIN_WITH_OTP = {
    "operationName": "LoginWithOTP",
    "query": (
        "mutation LoginWithOTP($email: String!, $otpCode: String!, $otpToken: String!) { "
        "loginWithOTP(email: $email, otpCode: $otpCode, otpToken: $otpToken) { __typename "
        "... on MobileLoginResponse { __typename accessToken refreshToken userSessionToken } } }"
    ),
}

VEHICLE_ORDERS = {
    "operationName": "vehicleOrders",
    "query": (
        "query vehicleOrders { orders(input: {orderTypes: [PRE_ORDER, VEHICLE], pageInfo: {from: 0, size: 10000}}) { "
        "__typename data { __typename id orderDate state configurationStatus fulfillmentSummaryStatus "
        "items { __typename sku } consumerStatuses { __typename isConsumerFlowComplete } } } }"
    ),
    "variables": {},
}

DELIVERY = {
    "operationName": "delivery",
    "query": (
        "query delivery($orderId: ID!) { delivery(orderId: $orderId) { __typename status carrier "
        "deliveryAddress { __typename addressLine1 addressLine2 city state country zipcode } "
        "appointmentDetails { __typename appointmentId startDateTime endDateTime timeZone } vehicleVIN } }"
    ),
}

_STEP_SEL = "{ sourceStatus { status details } consumerStatus { displayOrder current complete locked inProgress notStarted error } }"
_CORE_STEPS = ["titleAndReg", "tradeIn", "finance", "delivery", "insurance", "documentUpload", "contracts", "payment"]
_EXT_STEPS = _CORE_STEPS + ["taxCredit", "deliveryMethod", "deliveryScheduling", "financeAgreement", "rivianCare"]


def _tx(steps):
    body = " ".join(f"{s} {_STEP_SEL}" for s in steps)
    return {
        "operationName": "transactionStatus",
        "query": f"query transactionStatus($orderId: ID!) {{ transactionStatus(orderId: $orderId) {{ {body} }} }}",
    }


TRANSACTION_STATUS_CORE = _tx(_CORE_STEPS)
TRANSACTION_STATUS_EXT = _tx(_EXT_STEPS)

_ORDER_CORE_FIELDS = (
    "vin state orderDate orderCancelDate currency locale storeId type subtotal discountTotal taxTotal feesTotal "
    "paidTotal remainingTotal outstandingBalance costAfterCredits total "
    "billingAddress { firstName lastName line1 line2 city state country postalCode } "
    "shippingAddress { firstName lastName line1 line2 city state country postalCode } "
    "payments { id intent date method amount referenceNumber status card { last4 expiryDate brand } bank { bankName country last4 } transactionNotes } "
    "tradeIns { tradeInReferenceId amount } "
    "vehicle { vehicleId vin modelYear model make } "
    "items { id subtotal quantity title productId type unitPrice sku "
    "fees { items { description amount code type } total } "
    "configuration { configurationDate basePrice version options { optionId optionName groupId groupName price } } }"
)

_STEP_FLAGS = "{ displayOrder hide locked error notStarted inProgress completed allowEditPostCompletion dependsOnConsumerCompletion incompleteConsumerDependencies }"
_SUB = "{ status consumerStatus consumerSubStatus details }"

_ORDER_EXT_FIELDS = _ORDER_CORE_FIELDS + (
    " vehicleId userId fulfillmentSummaryStatus configurationStatus orderConfirmationType invitedToSelfConfirm invitedToSelfConfigure "
    "orderEmail garageLeaseStatus garageEstimatedInvitedToSelfConfigureDate garageInitialAgreementSignedAt "
    "consumerStatuses { isPreparingPurchase isConsumerFlowComplete "
    "steps { " + " ".join(f"{s} {_STEP_FLAGS}" for s in _EXT_STEPS) + " } "
    "titleAndReg " + _SUB + " tradeIn " + _SUB + " finance " + _SUB + " delivery " + _SUB + " insurance " + _SUB
    + " documentUpload " + _SUB + " contracts " + _SUB + " payment " + _SUB + " rivianCare " + _SUB + " }"
)

ORDER_CORE = {
    "operationName": "order",
    "query": f"query order($id: String!) {{ order(id: $id) {{ {_ORDER_CORE_FIELDS} }} }}",
}
ORDER_EXT = {
    "operationName": "order",
    "query": f"query order($id: String!) {{ order(id: $id) {{ {_ORDER_EXT_FIELDS} }} }}",
}

_REJECTED_RE = None


def rejected_fields(error_text: str) -> set[str]:
    """Field names from GraphQL validation errors like: Cannot query field "garageLeaseStatus" on type "Order"."""
    import re

    return set(re.findall(r'Cannot query field "([A-Za-z0-9_]+)"', error_text))


def strip_fields(query: str, names: set[str]) -> str:
    """Remove `name` or `name { ... }` selections (balanced braces) from a GraphQL document."""
    import re

    out = query
    for name in names:
        # name followed by a selection set
        pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"\s*\{")
        while True:
            m = pattern.search(out)
            if not m:
                break
            depth, i = 0, m.end() - 1
            while i < len(out):
                if out[i] == "{":
                    depth += 1
                elif out[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            out = out[: m.start()] + out[i + 1 :]
        # bare name (not followed by '(' or '{', not part of a longer identifier, not a keyword position)
        out = re.sub(r"(?<![A-Za-z0-9_$])" + re.escape(name) + r"(?![A-Za-z0-9_])(?!\s*[({:])", " ", out)
    return re.sub(r"\s+", " ", out).strip()


USER_INFO = {
    "operationName": "getUserInfo",
    "query": (
        "query getUserInfo { currentUser { __typename id email vehicles { __typename id name roles vin state createdAt updatedAt "
        "vehicle { __typename id vin modelYear make model expectedBuildDate plannedBuildDate "
        "expectedGeneralAssemblyStartDate actualGeneralAssemblyDate otaEarlyAccessStatus "
        "mobileConfiguration { __typename trimOption { optionId optionName } exteriorColorOption { optionId optionName } interiorColorOption { optionId optionName } } } } } }"
    ),
    "variables": {},
}
