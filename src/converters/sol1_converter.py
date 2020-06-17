import re
from keys.sol6_keys import *
from utils.dict_utils import *
from utils.key_utils import KeyUtils
from converters.etsi_nfv_vnfd import EtsiNfvVnfd, ToscaVnfd
from keys.sol6_keys import V2MapBase
from src.mapping_v2 import *
import yaml
import logging
log = logging.getLogger(__name__)


class Sol1Converter:

    def __init__(self, sol6_vnf, parsed_dict, variables):
        self.sol6_vnf = sol6_vnf["data"]["etsi-nfv-descriptors:nfv"]
        self.parsed_dict = parsed_dict
        self.tosca_vnfd = ToscaVnfd()
        self.variables = variables
        formatted_vars = PathMaping.format_paths(self.variables)

        self.va_t = formatted_vars["tosca"]
        self.va_s = formatted_vars["sol6"]
        self.mapping = []

    def convert(self):
        tv = self.get_tosca_value
        sv = self.get_sol6_value
        add_map = self.add_map

        vnfd = self.tosca_vnfd.vnfd

        # -- Metadata --
        add_map(((tv("vnf_provider"), V2MapBase.FLAG_BLANK),                sv("vnfd_provider")))
        add_map(((tv("vnf_product_name"), V2MapBase.FLAG_BLANK),            sv("vnfd_product")))
        add_map(((tv("vnf_software_ver"), V2MapBase.FLAG_BLANK),            sv("vnfd_software_ver")))
        add_map(((tv("vnf_desc_ver"), V2MapBase.FLAG_BLANK),                sv("vnfd_ver")))
        add_map(((tv("vnf_product_info_name"), V2MapBase.FLAG_BLANK),       sv("vnfd_info_name")))
        add_map(((tv("desc"), V2MapBase.FLAG_BLANK),                        sv("vnfd_info_desc")))
        add_map(((tv("vnf_vnfm_info"), V2MapBase.FLAG_BLANK),               sv("vnfd_vnfm_info")))
        # -- End Metadata --

        for (loc_put, flags), loc_get in self.mapping:
            set_path_to(loc_put, vnfd,
                        get_path_value(loc_get, self.sol6_vnf), create_missing=True)

        return vnfd

    def add_map(self, cur_map):
        self.mapping.append(cur_map)

    def get_tosca_value(self, value):
        return V2MapBase.get_value(value, self.va_t, "tosca")

    def get_sol6_value(self, value):
        return V2MapBase.get_value(value, self.va_s, "sol6")
