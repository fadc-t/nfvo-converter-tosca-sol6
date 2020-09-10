import re
from keys.sol6_keys import *
from utils.dict_utils import *
from utils.key_utils import KeyUtils
from converters.etsi_nfv_vnfd import EtsiNfvVnfd, ToscaVnfd
from converters.sol1_flags import *
from keys.sol6_keys import V2MapBase
from src.mapping_v2 import *
import yaml
import logging
log = logging.getLogger(__name__)


class Sol1Converter:

    def __init__(self, sol6_vnf, parsed_dict, variables):
        self.sol6_vnfd = sol6_vnf["data"]["etsi-nfv-descriptors:nfv"]
        self.parsed_dict = parsed_dict
        self.sol1_vnfd = ToscaVnfd().vnfd
        self.variables = variables
        formatted_vars = PathMapping.format_paths(self.variables)

        self.va_t = formatted_vars["tosca"]
        self.va_s = formatted_vars["sol6"]
        self.mapping = []
        self.v2_map = V2Mapping(self.sol1_vnfd, self.sol6_vnfd)
        self.sol1_flags = Sol1Flags(self.sol1_vnfd, self.sol6_vnfd)

    def convert(self):
        tv = self.get_tosca_value
        sv = self.get_sol6_value
        add_map = self.add_map

        vnfd = self.sol1_vnfd

        # -- Metadata --
        set_path_to(tv("vnf_type"), self.sol1_vnfd, "cisco.1VDU.1_0.1_0", create_missing=True)
        add_map(((tv("vnf_provider"), V2MapBase.FLAG_BLANK),                sv("vnfd_provider")))
        add_map(((tv("vnf_product_name"), V2MapBase.FLAG_BLANK),            sv("vnfd_product")))
        add_map(((tv("vnf_software_ver"), V2MapBase.FLAG_BLANK),            sv("vnfd_software_ver")))
        add_map(((tv("vnf_desc_ver"), V2MapBase.FLAG_BLANK),                sv("vnfd_ver")))
        add_map(((tv("vnf_product_info_name"), V2MapBase.FLAG_BLANK),       sv("vnfd_info_name")))
        add_map(((tv("desc"), V2MapBase.FLAG_BLANK),                        sv("vnfd_info_desc")))
        add_map(((tv("vnf_vnfm_info"), V2MapBase.FLAG_BLANK),               sv("vnfd_vnfm_info")))
        add_map(((tv("vnf_conf_autoheal"), V2MapBase.FLAG_BLANK),           sv("vnfd_config_autoheal")))
        add_map(((tv("vnf_conf_autoscale"), V2MapBase.FLAG_BLANK),          sv("vnfd_config_autoscale")))

        # -- End Metadata --

        # -- VDU --
        vdus = get_path_value(sv("vdus"), self.sol6_vnfd)
        print(vdus)
        # We need to duplicate the structure of the tosca vnfd vdu nodes for each entry greater than 1
        # if len(vdus) > 1:

        vdu_ids = [vdu["id"] for vdu in vdus]
        vdu_map = self.v2_map.generate_map_from_list(vdu_ids)
        print(vdu_map)

        # for cur_vdu_map in vdu_map:

        # add_map(((tv("vdu"), V2MapBase.FLAG_BLANK),                 [sv("vdu_id"), vdu_map]))
        # add_map(((tv("vdu_name"), self.FLAG_BLANK),                    [sv("vdu_name"), vdu_map]))
        # add_map(((tv("vdu_desc"), self.FLAG_BLANK),                    [sv("vdu_desc"), vdu_map]))

        self.run_mapping()
        # for (tosca_loc_put, flags), tosca_loc_get in self.mapping:
        #     if type(tosca_loc_get) is not list:
        #         set_path_to(tosca_loc_put, vnfd,
        #                     get_path_value(tosca_loc_get, self.sol6_vnfd), create_missing=True)
        #     else:  # We have a mapping to deal with
        #         [sol6_loc_get, mapping] = tosca_loc_get
        #         for cur_map in mapping:
        #             tosca_fmt = MapElem.format_path(cur_map, tosca_loc_put, use_value=False)
        #             sol6_fmt = MapElem.format_path(cur_map, sol6_loc_get, use_value=True)
        #             set_path_to(tosca_fmt, vnfd,
        #                         get_path_value(sol6_fmt, self.sol6_vnfd), create_missing=True)

        return vnfd

    # *************************
    # ** Run Mapping Methods **
    # *************************
    def run_mapping(self):
        """
        The first parameter is always a tuple, with the flags as the second parameter
        If there are multiple flags, they will be grouped in a tuple as well
        """
        for ((sol1_path, flags), map_sol6) in self.mapping:
            self.run_mapping_flags(flags, V2MapBase)
            self.run_mapping_map_needed(sol1_path, map_sol6)

    def run_mapping_flags(self, flags, flag_consts: V2MapBase):
        """
        Handle various flag operations, such as setting them to false and updating their values
        Called from run_mapping
        """
        self.sol1_flags.set_flags_false()
        self.sol1_flags.set_flags_loop(flags, flag_consts)

    def run_mapping_map_needed(self, sol1_path, map_sol6):
        """
        Determine if a mapping (list of MapElem) has been specified
        Called by run_mapping
        """
        if sol1_path is None:
            log.debug("SOL1 path is None, skipping with no error message")
            return

        log.debug("Run mapping for sol6: {} --> tosca: {}"
                  .format(map_sol6 if not isinstance(map_sol6, list) else map_sol6[0], sol1_path))

        # Check if there is a mapping needed
        if isinstance(map_sol6, list):
            log.debug("\tMapping: {}".format(map_sol6[1]))
            self.run_mapping_islist(sol1_path, map_sol6)
        else:  # No mapping needed
            self.run_mapping_notlist(sol1_path, map_sol6)

    def run_mapping_islist(self, tosca_path, map_sol6):
        """
        What to do if there is a complex mapping needed
        Called from run_mapping_map_needed
        """
        mapping_list = map_sol6[1]  # List of MapElems
        sol6_path = map_sol6[0]

        for elem in mapping_list:
            # Skip this mapping element if it is None, but allow a none name to pass
            if not elem:
                continue
            if not elem.parent_map and self.sol1_flags.req_parent:
                if not self.sol1_flags.fail_silent:
                    log.warning("Parent mapping is required, but {} does not have one".format(elem))
                continue

            tosca_use_value = self.sol1_flags.tosca_use_value
            f_tosca_path = MapElem.format_path(elem, tosca_path, use_value=tosca_use_value)
            f_sol6_path = MapElem.format_path(elem, sol6_path, use_value=True)

            log.debug("Formatted paths:\n\ttosca: {} --> sol6: {}"
                      .format(f_tosca_path, f_sol6_path))

            # Handle flags for mapped values
            value = self.sol1_flags.handle_flags(f_sol6_path, f_tosca_path)

            # If the value doesn't exist, don't write it
            # Do write it if the value is 0, though
            write = True
            if not value:
                write = True if value is 0 else False

            if write:
                set_path_to(f_tosca_path, self.sol1_vnfd, value, create_missing=True)

    def run_mapping_notlist(self, sol1_path, map_sol6):
        """
        What to do if there is no complex mapping specified
        Called from run_mapping_map_needed
        """
        sol6_path = map_sol6
        if sol6_path is None:
            log.debug("SOL6 path is None, skipping with no error message")
            return

        # Handle the various flags for no mappings
        value = self.sol1_flags.handle_flags(sol6_path, sol1_path)

        set_path_to(sol1_path, self.sol1_vnfd, value, create_missing=True)

    def add_map(self, cur_map):
        self.mapping.append(cur_map)

    def get_tosca_value(self, value):
        return V2MapBase.get_value(value, self.va_t, "tosca")

    def get_sol6_value(self, value):
        return V2MapBase.get_value(value, self.va_s, "sol6")

    @staticmethod
    def set_value(val, path, index, prefix_value=None, prefix_index=None):
        """
        TODO: Note: this does not work for sol1 yet
        Wrapper to more easily set a values when applying mappings
        """
        _mapping = MapElem(val, index)
        if prefix_value is not None or prefix_index is not None:
            _mapping = MapElem(prefix_value, prefix_index, parent_map=_mapping)
        return (val, V2MapBase.FLAG_KEY_SET_VALUE), [path, [_mapping]]
