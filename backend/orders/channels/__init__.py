"""Report parsers for the online sales channels, keyed by `SALES_CHANNELS`.

Each takes the uploaded file's name and bytes and returns a `ChannelReport`,
or raises `ChannelImportError`.
"""

from orders.channels import mercadolibre

PARSERS = {
    "MERCADOLIBRE": mercadolibre.parse,
}
