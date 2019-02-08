import copy
from sol6_keys import TOSCA, SOL6, KeyUtils


class Sol6Converter:
    tosca_vnf = None
    parsed_dict = None
    vnfd = None
    template_inputs = {}
    log = None

    def __init__(self, tosca_vnf, parsed_dict, log=None):
        self.tosca_vnf = tosca_vnf
        self.parsed_dict = parsed_dict
        self.log = log
        # Set this up for _virtual_get_flavor_names
        self.flavor_names = []
        self.connection_points = {}
        self.tosca_vdus = {}
        self.flavor_vars = {}

    def parse(self):
        """
        Convert the tosca_vnf to sol6 VNFD
        Currently only handles converting a single VNF to VNFD
        """
        # TODO: Handle multiple vnfds
        # First, get the vnfd specifications model
        self.vnfd = copy.deepcopy(self.parsed_dict[SOL6.vnfd])

        # Get all of the inputs from tosca
        self.template_inputs = self.path_to_value(TOSCA.inputs, self.tosca_vnf)

        self._handle_one_to_one()
        self._handle_virtual_compute()
        self._handle_virtual_link()
        self._remap_vdus()
        self._handle_connection_point()

        return self.vnfd

    def _handle_virtual_compute(self):
        """
        Get the list of vim flavor names that are in use, and find their properties
        """
        self._virtual_get_flavor_names()
        self._process_vdus()
        self._populate_init_level()
        self._populate_scaling_aspects()
        self._virtual_storage_set_capabilities()

        compute_descriptors = self.path_to_value(SOL6.virtual_comp_desc, self.vnfd)
        node_list = []

        for c in compute_descriptors:
            node_list.append(c[get_dict_key(c)])

        set_path_to(SOL6.virtual_comp_desc, self.vnfd, node_list)

    def _virtual_get_flavor_names(self):
        """
        We need to get the flavor names from all the VDUs
        Now take those values and combine them with the compute descriptors and set them.
        """

        # Get the information about the VDUs into a list of dicts
        self.tosca_vdus = get_roots_from_filter(self.tosca_vnf, child_key='type',
                                                child_value=TOSCA.vdu_type)
        for vdu in self.tosca_vdus:
            # First, get the value of the vim_flavor for this VDU, don't parse it yet
            vim_flavor = self.path_to_value(TOSCA.vdu_vim_flavor.format(get_dict_key(vdu)),
                                            self.tosca_vnf)
            self.flavor_names.append(vim_flavor)

        # Save this data on flavors for later use
        self.flavor_vars = {}

        # Flavor names and information can be either a variable (from inputs) or it can be hardcoded
        # which means that we need to handle getting data from both inputs and also locally
        for flavor in self.flavor_names:
            if is_tosca_input(flavor):
                # Get the variable name + flavor data from the template inputs
                name, data = self.tosca_get_input(flavor)
            else:
                # The information is not coming from an input, so handle it all here
                # This works if it's formatted as follows:
                #             vim_flavor: "ab-auto-test-vnfm3-control-function"
                name = flavor
                data = flavor

            self.flavor_vars[name] = data

        compute_descriptors = []

        # Loop through our data and create the paths and values in a temp dict, then append it to
        # the final list
        for name, data in self.flavor_vars.items():
            cur_dict = {}
            set_path_to(KeyUtils.remove_path_elem(SOL6.vcd_id, 0), cur_dict, name.lower(),
                        create_missing=True)
            set_path_to(KeyUtils.remove_path_elem(SOL6.vcd_flavor_name, 0), cur_dict, name,
                        create_missing=True)
            compute_descriptors.append(cur_dict)

        # Put the final list into the key of the dict
        set_path_to(SOL6.virtual_comp_desc, self.vnfd, compute_descriptors)

    def _process_vdus(self):
        """
        There are multiple methods that need data from VDUs, and there's no need to loop through
        them more than once, at least not right now.
        """
        virt_compute_descriptors = self.path_to_value(SOL6.virtual_comp_desc, self.vnfd)

        # Make a list for deployment-flavors vdu-profiles
        df_vdu_prof = []

        # First get all groups, so we can pass it in to _handle_vdu_profile later
        groups = get_roots_from_filter(self.tosca_vnf, child_key="type",
                                       child_value=TOSCA.group_affinity_type)

        anti_affinity_policies = get_roots_from_filter(self.tosca_vnf, child_key="type",
                                                       child_value=TOSCA.anti_affinity_type)

        for vdu in self.tosca_vdus:
            vdu_name = get_dict_key(vdu)

            # Populate the configurable properties
            self._handle_config_params(vdu_name)

            # Set the virtual capabilities
            # To do this we need to match the entries from compute_descriptors to the ones in vdus
            self._virtual_compute_set_capabilities(vdu, virt_compute_descriptors)

            # Populate the list of vdu profiles
            df_vdu_prof.append(self._populate_vdu_profile(vdu_name, groups, anti_affinity_policies))

        set_path_to(SOL6.df_vdu_profile, self.vnfd, df_vdu_prof)

        self._populate_init_affinity(anti_affinity_policies, groups)

    def _populate_init_level(self):
        """
        Populate the <instantiation-level> data, includes scaling info
        """
        # Get the InstationationLevels data
        i_levels_data = get_roots_from_filter(self.tosca_vnf, child_key="type",
                                              child_value=TOSCA.instan_level_type)

        nfv_policy_info = get_roots_from_filter(self.tosca_vnf, child_key="type",
                                                child_value=TOSCA.instan_level_nfv_type)

        # ------------------------------------------------------------------------------------------
        def _get_desc_from_level(outer_level_name):
            """
            Loop through all the nfv InstantiationLevel entries.
            We want to match the levels: level_name to our cur_level, and then get the description
            of that key
            nfv_policy_info comes from outside this method
            """
            for nfv_p in nfv_policy_info:
                nfv_p = nfv_p[get_dict_key(nfv_p)]
                nfv_levels = self.path_to_value(KeyUtils.remove_path_first(TOSCA.instan_level_nfv),
                                                nfv_p)
                # We want to loop over all the levels, but if there's only one element it's a dict
                # so if there is only 1, make it into a list
                if not isinstance(nfv_levels, list):
                    # Convert a dict with multiple keys into a list of multiple dicts
                    nfv_levels = [{k: v} for k, v in nfv_levels.items()]

                # Look for a match of the level_names, then set cur_desc if we find it
                for level in nfv_levels:
                    cur_level_name = get_dict_key(level)
                    if cur_level_name == outer_level_name:
                        return level[outer_level_name][KeyUtils.get_path_last(
                            TOSCA.instan_level_nfv_desc)]
            return None
        # ------------------------------------------------------------------------------------------

        df_inst_rm_path = KeyUtils.get_path_level(SOL6.df_inst_level)
        inst_parsed = []

        for inst_level in i_levels_data:
            cur_name = get_dict_key(inst_level)
            cur_level = get_dict_key(self.path_to_value(TOSCA.instan_levels.format(cur_name),
                                                        inst_level))
            cur_targets = self.path_to_value(TOSCA.instan_level_targets.format(cur_name),
                                             inst_level)
            cur_num_inst = self.path_to_value(
                TOSCA.instan_level_num.format(cur_name, cur_level), inst_level)

            cur_desc = _get_desc_from_level(cur_level)

            for target in cur_targets:
                c = {}
                # Remove the vnfd.df.instantiation-level before the keys,
                # since we're building a list and will put it in that location at the end
                set_path_to(KeyUtils.remove_path_first(
                    SOL6.df_inst_level_id, df_inst_rm_path),
                    c, cur_level, create_missing=True)

                if cur_desc is not None:
                    set_path_to(KeyUtils.remove_path_first(
                        SOL6.df_inst_level_desc, df_inst_rm_path),
                        c, cur_desc, create_missing=True)
                else:
                    print("A matching description was not found for {}.".format(cur_level))

                set_path_to(KeyUtils.remove_path_first(
                    SOL6.df_inst_level_vdu, df_inst_rm_path),
                    c, target, create_missing=True)

                set_path_to(KeyUtils.remove_path_first(
                    SOL6.df_inst_level_num, df_inst_rm_path),
                    c, cur_num_inst, create_missing=True)

                inst_parsed.append(c)

        set_path_to(SOL6.df_inst_level, self.vnfd, inst_parsed)

    def _populate_scaling_aspects(self):
        """
        Get information from the scaling_aspect policy, then populate
        <instantiation-level><scaling-info>
        and
        <df><scaling-aspect>
        At the same time
        """
        scaling_props = get_roots_from_filter(self.tosca_vnf, child_key="type",
                                              child_value=TOSCA.scaling_aspect_type)

        # ------------------------------------------------------------------------------------------
        # Populate instantiation-level scaling info
        inst_scalings = []
        df_scalings = []
        df_inst_rm_path = KeyUtils.get_path_level(SOL6.df_inst_level)
        for policy in scaling_props:
            policy_name = get_dict_key(policy)
            policy = policy[policy_name]

            aspects_path = KeyUtils.remove_path_first(TOSCA.scaling_aspects.format(policy_name))
            aspects = self.path_to_value(aspects_path, policy)

            # Convert this into a list of dicts with single elements
            aspects = [{k: v} for k, v in aspects.items()]

            for aspect in aspects:
                # Init the blank dicts for these elements
                scale_entry = {}
                inst_entry = {}

                # Define a lambda to remove the first bits of the path that we don't need rn
                path_form = lambda x: KeyUtils.remove_path_first(
                    x, KeyUtils.get_path_level(TOSCA.scaling_aspects))

                # Get the values for the paths
                aspect_name = get_dict_key(aspect)
                # For these two we have two values that need to be formatted, but we're immediately
                # removing the first one. There should be a better way to do this that doesn't
                # required a confusing lambda expression, but I can't think of it right now.
                cur_desc = self.path_to_value(path_form(TOSCA.scaling_aspect_desc
                                                        .format("", aspect_name)), aspect)
                max_scale = self.path_to_value(path_form(TOSCA.scaling_aspect_max_level
                                                         .format("", aspect_name)), aspect)
                inst_scale_level = 0

                # Populate the instantiation scaling info
                set_path_to(KeyUtils.remove_path_first(SOL6.df_inst_scale_aspect, df_inst_rm_path),
                            inst_entry, aspect_name, create_missing=True)
                set_path_to(KeyUtils.remove_path_first(SOL6.df_inst_scale_level, df_inst_rm_path),
                            inst_entry, inst_scale_level, create_missing=True)

                # Populate the entries for the scaling-aspect entries
                set_path_to(KeyUtils.remove_path_first(SOL6.df_scaling_id, df_inst_rm_path),
                            scale_entry, aspect_name, create_missing=True)
                set_path_to(KeyUtils.remove_path_first(SOL6.df_scaling_name, df_inst_rm_path),
                            scale_entry, aspect_name, create_missing=True)
                set_path_to(KeyUtils.remove_path_first(SOL6.df_scaling_desc, df_inst_rm_path),
                            scale_entry, cur_desc, create_missing=True)
                set_path_to(KeyUtils.remove_path_first(SOL6.df_scaling_max_scale, df_inst_rm_path),
                            scale_entry, max_scale, create_missing=True)

                inst_scalings.append(inst_entry)
                df_scalings.append(scale_entry)

        # We need to add a list to an existing list of dicts
        # There is currently not support for this built in to set_path_to, so we need to
        # extract the list and iterate over it manually
        existing_inst_dict = self.path_to_value(SOL6.df_inst_level, self.vnfd)
        for e in existing_inst_dict:
            set_path_to(KeyUtils.get_path_last(SOL6.df_inst_scale_info), e, inst_scalings,
                        create_missing=True)
        # Because of how python works we don't need to re-set the dict, we can just it and it'll
        # keep the changes in the main dict
        # ------------------------------------------------------------------------------------------
        # Populate df scaling-aspects
        set_path_to(SOL6.df_scaling_aspect, self.vnfd, df_scalings)

    def _populate_init_affinity(self, anti_affinity_policies, groups):
        """
        Populate the instatiation-level (df.) anti-affinity-groups info
        We need the id, type, and scope of each group
        All the data we need is in anti_affinity_policies
        """
        # First off, get the full list of groups
        group_names = []
        for group in groups:
            group_names.append(get_dict_key(group))

        init_aff = []
        for policy in anti_affinity_policies:
            policy = policy[get_dict_key(policy)]

            # We are going to pop the group name out of this list when we've populated it
            # So if there are multiple policies that target one group, we are only going to take
            # the first one
            targets = policy[TOSCA.policy_aff_targets_key]

            try:
                for target in targets:
                    # Throws ValueError if the element is not in the list
                    group_names.remove(target)
            except ValueError:
                # Skip the outer loop if we have already found the group
                continue

            for target in targets:
                # Construct the dict of values that we need to get
                typ = policy[TOSCA.policy_aff_type_key]
                c = {KeyUtils.get_path_last(SOL6.df_affinity_group_id): target,
                     KeyUtils.get_path_last(SOL6.df_affinity_group_type):
                         SOL6.df_anti_affinity_value(typ),
                     KeyUtils.get_path_last(SOL6.df_affinity_group_scope):
                         self.path_to_value(TOSCA.policy_aff_scope_key, policy)}

                # Put them in the outer list
                init_aff.append(c)

        set_path_to(SOL6.df_affinity_group, self.vnfd, init_aff)

    def _populate_vdu_profile(self, vdu_name, groups, anti_aff_rules):
        """
        Populate the vdu id, min/max num of instances, then link the policies to the VDUs through
        the groups in TOSCA.
        We will then have enough information to set the affinity-or-anti-affinity-group(s)
        in the VDU and also outside of it
        :return: A dict of values for the instance
        """
        prof = {KeyUtils.get_path_last(SOL6.df_id): vdu_name,
                KeyUtils.get_path_last(SOL6.df_vdu_p_min):
                    self.path_to_value(TOSCA.vdu_profile_min.format(vdu_name), self.tosca_vnf),

                KeyUtils.get_path_last(SOL6.df_vdu_p_max):
                    self.path_to_value(TOSCA.vdu_profile_min.format(vdu_name), self.tosca_vnf)
                }

        in_groups = []
        # Find all the affinity groups that this vdu is a part of
        for g in groups:
            # The entries are all in their own dicts with a single key and entry, so get the name
            # of the group and then get the data inside of it
            name = get_dict_key(g)
            g = g[name]
            if vdu_name in g[TOSCA.group_aff_members_key]:
                in_groups.append(name)

        rules = []
        for rule in anti_aff_rules:
            name = get_dict_key(rule)
            rule = rule[name]
            targets = rule[TOSCA.policy_aff_targets_key]

            # We have the targets for each rule, now loop through them and see if any of them
            # are in our in_group, if they are add it to our rules list
            for target in targets:
                if target in in_groups:
                    rules.append(target)

        # Create the 'affinity-or-anti-affinity':'id': [ rules ] dict structure and add it to our
        # profile dict
        set_path_to(KeyUtils.get_path_last(SOL6.df_vdu_p_aff_id, 2), prof, rules,
                    create_missing=True)

        return prof

    def _virtual_compute_set_capabilities(self, vdu, compute_descriptors):
        """
        Link the flavor names with the given VDU from TOSCA, then get the number of virtual CPUs
        and memory size and place it in the relevant entry in the list at compute_descriptors
        This is the interior of a loop.
        """

        # There very strongly should only be one key in each of these dicts
        # This is now the full path of the vdu
        name = get_dict_key(vdu)

        # Get the name of the flavor of the VDU
        vim_flavor = self.path_to_value(TOSCA.vdu_vim_flavor.format(name), self.tosca_vnf)

        if TOSCA.from_input in vim_flavor:
            # If this is from an input get the interior variable
            vim_flavor = vim_flavor[TOSCA.from_input]
        else:
            if not is_hashable(vim_flavor):
                raise ValueError("vim_flavor: {} is not a recognized format.".format(vim_flavor))

        num_cpu = self.path_to_value(TOSCA.vdu_num_cpu.format(name), self.tosca_vnf)
        mem_size = self.path_to_value(TOSCA.vdu_mem_size.format(name), self.tosca_vnf)

        # Remove the 'GB' from the end and only keep the number
        mem_size = mem_size.split(" ")[0]

        # Find the compute_descriptor that matches the vim_flavor
        comp = None
        for c in compute_descriptors:
            if self.path_to_value(KeyUtils.remove_path_elem(SOL6.vcd_flavor_name, 0),
                                  c) == vim_flavor:
                comp = c
                break

        if comp is None:
            raise ValueError("A matching compute_descriptor was not found for {}."
                             .format(vim_flavor))
        # Work with the relative path here, since we're still in a list
        set_path_to(KeyUtils.remove_path_elem(SOL6.vcd_virtual_cpu, 0),
                    comp, num_cpu, create_missing=True)
        set_path_to(KeyUtils.remove_path_elem(SOL6.vcd_virtual_memory, 0),
                    comp, mem_size, create_missing=True)

    def _handle_config_params(self, vdu_name):
        """
        Get all the additional properties and put them in a list at configurable-properties
        """

        # Now get the rest of the configurable parameters
        # TODO

    def _virtual_storage_set_capabilities(self):
        """
        Set the virtual block storage information
        """
        vbs = get_roots_from_filter(self.tosca_vnf, child_key="type",
                                    child_value=TOSCA.vbs_type)

        res_list = []
        # There needs to be multiple entries with the same name, so we're saving this in a
        # list and handling writing it properly later
        for cur_vb in vbs:
            name = get_dict_key(cur_vb)
            storage_size = self.path_to_value(TOSCA.vbs_size.format(name), self.tosca_vnf)
            storage_size = storage_size.split(" ")[0]  # Remove 'GB' from the end

            dic = {KeyUtils.get_path_last(SOL6.vsd_id): name,
                   # KeyUtils.get_path_last(SOL6.vsd_type_storage): SOL6.vsd_type_storage_value,
                   KeyUtils.get_path_last(SOL6.vsd_size_storage): storage_size}

            res_list.append(dic)

        set_path_to(SOL6.virtual_storage_desc, self.vnfd, res_list)

    def _handle_virtual_link(self):
        """
        Set the data for the virtual link mapping.
        """
        links = get_roots_from_filter(self.tosca_vnf, child_key="type",
                                      child_value=TOSCA.vlm_type)
        res_list = []
        # There needs to be multiple entries with the same name, so we're saving this in a
        # list and handling writing it properly later
        for cur_link in links:
            name = get_dict_key(cur_link)
            desc = self.path_to_value(TOSCA.vlm_desc.format(name), self.tosca_vnf)
            protocols = self.path_to_value(TOSCA.vlm_protocols.format(name), self.tosca_vnf)
            protocol = protocols

            # The normal type of protocols is list, so get the first element for now
            if isinstance(protocol, list):
                protocol = protocols.pop()

            dic = {}
            set_path_to(KeyUtils.get_path_last(SOL6.vld_id), dic, name, create_missing=True)
            set_path_to(KeyUtils.get_path_last(SOL6.vld_desc), dic, desc, create_missing=True)
            set_path_to(KeyUtils.get_path_last(
                SOL6.vld_protocol, 2), dic, protocol, create_missing=True)

            res_list.append(dic)

        set_path_to(SOL6.virtual_link_desc, self.vnfd, res_list, create_missing=True)

    def _handle_deployment_flavor(self):
        pass

    def _handle_connection_point(self):
        """
        Read the substitution_mappings, determine which connection points are management and
        which are not.
        """
        # Read substitution_mappings
        sub_mappings = self.path_to_value(TOSCA.substitution_mappings, self.tosca_vnf)
        # Read the entries that match the keys in TOSCA.sub_link_types
        # The entry should be a list [endpoint_name, endpoint_type]
        accepted_cps = [sub[get_dict_key(sub)] for sub in sub_mappings
                        if get_dict_key(sub) in TOSCA.sub_link_types]

        # Set up management and vim_orchestration slots
        self.connection_points[SOL6.cp_mgmt_key] = []
        self.connection_points[SOL6.cp_vim_orch_key] = []

        # Read the connection point info
        for cp in accepted_cps:
            cp_info = self.path_to_value(TOSCA.connection_point.format(cp[0]), self.tosca_vnf)
            is_mgmt = self.path_to_value(KeyUtils.remove_path_level(
                TOSCA.cp_management, TOSCA.connection_point), cp_info)

            # Just in case it's a string for some reason
            if not isinstance(is_mgmt, bool):
                is_mgmt = (is_mgmt == "True")

            if is_mgmt:
                k = SOL6.cp_mgmt_key
            else:
                k = SOL6.cp_vim_orch_key
            self.connection_points[k].append(cp_info)

        # We have already populated the virtual link description fields
        # Now we need to create internal CPs that link to the VLs, and external CPs that also
        # link to the VLs, so that way we can have multiple int-cpd mapped to one ext-cpd

        virtual_links = self.path_to_value(SOL6.virtual_link_desc, self.vnfd)

        if not isinstance(virtual_links, list) or len(virtual_links) < 2:
            raise KeyError("There are not enough virtual links defnied in the TOSCA file to map"
                           "the required internal/external connection points.")

        # We will take the first link for use in management
        mgmt_link = virtual_links[0]
        # And the second for orchestration
        orch_link = virtual_links[1]

        def _populate_cp_type(cp_list, link):
            # Loop through the mgmt connection points
            for int_cp in cp_list:
                cur_path = KeyUtils.remove_path_level(TOSCA.cp_virt_binding, TOSCA.connection_point)
                assoc_vdu = self.path_to_value(cur_path, int_cp)
                # Get the VDU dict
                cur_vdu = None
                for vdu in self.tosca_vdus:
                    if assoc_vdu in vdu:
                        cur_vdu = vdu
                        break
                if not cur_vdu:
                    raise KeyError("VDU {} not found in VDUList {}".format(assoc_vdu,
                                                                           self.tosca_vdus))

                # Handle adding multiple links
                cur_value = None
                try:
                    cur_value = self.path_to_value(SOL6.int_cp.format(assoc_vdu), cur_vdu)
                except KeyError:
                    pass
                if not isinstance(cur_value, list):
                    cur_value = []
                # Populate the connection point info
                cur_id = assoc_vdu + "_" + link[TOSCA.cp_virt_link_id_key]
                cur_link_desc = link[TOSCA.cp_virt_link_desc_key]
                formatted_link = {KeyUtils.get_path_last(SOL6.int_cp_id): cur_id,
                                  KeyUtils.get_path_last(SOL6.int_cp_link_desc): cur_link_desc}

                # Check if the current id is already present in the values of the current list
                if not any(cur_id in y for y in (list(x.values()) for x in cur_value)):
                    cur_value.append(formatted_link)

                set_path_to(SOL6.int_cp.format(assoc_vdu), cur_vdu, cur_value,
                            create_missing=True)

        _populate_cp_type(self.connection_points[SOL6.cp_mgmt_key], mgmt_link)
        _populate_cp_type(self.connection_points[SOL6.cp_vim_orch_key], orch_link)

        # Now write the ext-cpd with the SOL6.ext_cp_mgmt_id
        ext_cps = [{
            KeyUtils.get_path_last(SOL6.ext_cp_id): SOL6.ext_cp_mgmt_id,
            KeyUtils.get_path_last(SOL6.ext_cp_int_cp): mgmt_link[TOSCA.cp_virt_link_desc_key],
            KeyUtils.get_path_last(SOL6.ext_cp_layer_protocol): SOL6.ext_cp_layer_protocol_value
        }, {
            KeyUtils.get_path_last(SOL6.ext_cp_id): SOL6.ext_cp_orch_id,
            KeyUtils.get_path_last(SOL6.ext_cp_int_cp): orch_link[TOSCA.cp_virt_link_desc_key],
            KeyUtils.get_path_last(SOL6.ext_cp_layer_protocol): SOL6.ext_cp_layer_protocol_value
        }]

        set_path_to(SOL6.ext_cp, self.vnfd, ext_cps)

    def _remap_vdus(self):
        """
        Remap self.tosca_vdus into SOL6 format
        Currently they all have the data loaded in as YAML format
        """



    def _handle_vnf_nfvo(self):
        pass

    def _handle_one_to_one(self):
        """
        Locate and assign the strict 1-to-1 value mappings
        """

        tosca_members = get_object_keys(TOSCA)
        sol6_members = get_object_keys(SOL6)
        # Get the intersection of the two sets of members
        valid_keys = [key for key in tosca_members if key in sol6_members]
        value_keys = [key for key in sol6_members if key + SOL6.value_key in sol6_members]

        for key in valid_keys:
            set_path_to(getattr(SOL6, key), self.vnfd,
                        self.path_to_value(getattr(TOSCA, key), self.tosca_vnf))

        for key in value_keys:
            set_path_to(getattr(SOL6, key), self.vnfd, getattr(SOL6, key + SOL6.value_key))

    def tosca_get_input(self, input_name):
        """
        Attempt to locate and return the value of the given input from the tosca vnf file
        :param input_name: { 'get_input': 'VAR_NAME' }
        :returns: (var_name, data) or (None, None)
        """
        if not is_tosca_input(input_name):
            return None, None

        data = self.template_inputs[input_name[TOSCA.from_input]]
        name = input_name[TOSCA.from_input]

        return name, data

    def path_to_value(self, path, cur_dict, map_inputs=False):
        """
        topology_template.node_templates.vnf.properties.descriptor_id
        Pass in a path and a dict the path applies to and get the value of the key

        This is experimental, and disabled for now.
        If map_inputs is set, the method will check to see if the final result
        is an input from TOSCA, and if so it will try to return that instead of the
        raw value.
        """
        values = path.split(".")
        cur_context = cur_dict

        for val in values:
            if isinstance(cur_context, list):
                cur_context = cur_context[0]

            if val in cur_context:
                cur_context = cur_context[val]
            else:
                raise KeyError("Specified path/key '{}' "
                               "not found in '{}'".format(val, list(cur_dict.keys())[0]))

        # We're only going to support automatically mapping get_inputs when they are the final value
        if map_inputs and is_tosca_input(cur_context):
            # For now, just return the name and not the data of the variable.
            name, _ = self.tosca_get_input(cur_context)
            cur_context = name

        return cur_context


# ******* Static Methods ********
def is_tosca_input(val):
    try:
        return TOSCA.from_input in val
    except TypeError:
        return False


def set_path_to(path, cur_dict, value, create_missing=False, list_elem=0):
    """
    Sets the value of path inside of cur_dict to value
    If create_missing is set then it will create all the required dicts to make the assignment true

    If a list is encountered and set_all_lists is false, then the method will pick list_elem
    in the list and continue with that as the context.
    """
    values = path.split(".")
    cur_context = cur_dict
    i = 0
    while i < len(values):
        # When we encounter a list, get the list_elem (default the first) and continue
        if isinstance(cur_context, list):
            cur_context = cur_context[list_elem]

        if values[i] in cur_context:
            if values[i] == values[-1]:
                cur_context[values[i]] = value
                break

            if not cur_context[values[i]] and create_missing:
                cur_context[values[i]] = {}
            cur_context = cur_context[values[i]]

        else:  # Enforce strict structure
            if create_missing:  # If we want to create the keys as we find they are missing
                cur_context[values[i]] = ''
                i -= 1  # Put the loop back by 1
            else:
                raise KeyError("Specified path/key {} not found in {}"
                               .format(values[i], list(cur_dict.keys())[0]))
        i += 1


def get_roots_from_filter(cur_dict, child_key=None, child_value=None, parent_key=None,
                          internal_call=False, agg=None):
    """
    We need to be able to get root elements based on some interior condition, for example:
    
    VDU c1 has a type of 'cisco.nodes.nfv.Vdu.Compute', so we need to be able to get all the VDUs
    based on this type and value.

    This method returns a single list of the elements that meet the conditions. It performs
    aggregation along the way and returns the aggregated list at the end of the recursion.

    :return: A single list of dicts that satisfies the conditions
    """
    # Recursively search through the dict since it's a large nested dict of other dicts
    # and lists and values
    if agg is None:
        agg = []

    # Stop if we get too far in to the data and don't know how to handle it
    if not isinstance(cur_dict, dict):
        return None

    for key, value in cur_dict.items():
        # This only searches by key and/or value
        # Base cases
        # TODO: Simplify
        if child_key and child_key == key:
            if not child_value:
                if parent_key:
                    return {parent_key: cur_dict}
                return cur_dict
            # else
            if child_value == value:
                if parent_key:
                    return {parent_key: cur_dict}
                return cur_dict

        # This is the actual recursion and aggregation bit, a list is kept and passed
        # around that only has dicts in it, and eventually it gets to the top and is returned
        # We call this in two different places, which is why it's extracted into a method
        # There is probably a better way to do this than to have another internal method, though

        # Handle if we have a list of dicts
        if isinstance(value, list):
            for i in range(len(value)):
                r = get_roots_from_filter(value[i], child_key, child_value,
                                          internal_call=True, agg=agg)
                # Only add to the results list if we have a valid output
                if r:
                    agg.append(r)
        else:
            if isinstance(value, dict):
                res = get_roots_from_filter(cur_dict[key], child_key, child_value, key,
                                            internal_call=True, agg=agg)
                if isinstance(res, dict):
                    agg.append(res)
                elif isinstance(res, list) and len(res) > 0:
                    for e in res:
                        if e:
                            agg.append(e)

    # Keep track of if we are calling this method internally, and if we reach the endpoint where we
    # are not, that means we're at the top level of recursion, about to finish.
    # If that is the case then return our aggregated list, since we need to give it back to the
    # calling point
    if not internal_call:
        return agg


def get_dict_key(dic, n=0):
    """
    Return the first (or nth) key name from a dict
    """
    return list(dic.keys())[n]


def get_object_keys(obj):
    return [attr for attr in dir(obj) if not callable(getattr(obj, attr)) and
            not (attr.startswith("__") or attr.startswith("_"))]


def is_hashable(obj):
    """Determine whether 'obj' can be hashed."""
    try:
        hash(obj)
    except TypeError:
        return False
    return True