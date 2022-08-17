import concurrent.futures
import errno
import json
from lxml import etree
from pathlib import Path
import os
from osctiny import Osc
import re

class IBS:
    def __init__(self, cfg):
        self.cfg = cfg
        self.osc = Osc(url='https://api.suse.de')

        ibs_user = re.search('(\w+)@', cfg.email).group(1)
        self.prj_prefix = 'home:{}:klp'.format(ibs_user)

        self.cs_data = {
                'kernel-default' : '(kernel-default\-(extra|(livepatch-devel|kgraft)?\-?devel)?\-?[\d\.\-]+.x86_64.rpm)',
                'kernel-source' : '(kernel-(source|macros|devel)\-?[\d\.\-]+.noarch.rpm)'
        }

    def do_work(self, func, args):
        if len(args) == 0:
            return

        with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
            results = executor.map(func, args)
            for result in results:
                if result:
                    print(result)

    # The projects has different format: 12_5u5 instead of 12.5u5
    def get_projects(self):
        return self.osc.search.project("starts-with(@name, '{}')".format(self.prj_prefix))

    def get_project_names(self):
        names = []
        for result in self.get_projects().findall('project'):
            names.append(result.get('name'))

        return names

    def delete_project(self, prj):
        if not self.osc.projects.exists(prj):
            return

        ret = self.osc.projects.delete(prj)
        if type(ret) is not bool:
            print(etree.tostring(ret))
            raise ValueError(prj)

        print('\t' + prj)

    def download_cs_data(self, cs):
        jcs = self.cfg.codestreams[cs]

        prj = jcs['project']
        if not jcs['update']:
            repo = 'standard'
        else:
            repo = 'SUSE_SLE-{}'.format(jcs['sle'])
            if jcs['sp']:
                repo = '{}-SP{}'.format(repo, jcs['sp'])
            repo = '{}_Update'.format(repo)

        path_dest = Path(self.cfg.kernel_rpms, jcs['cs'])
        path_dest.mkdir(exist_ok=True)

        print('Downloading {} packages into {}'.format(cs, str(path_dest)))
        for k, regex in self.cs_data.items():
            pkg = '{}.{}'.format(k, repo)

            rpms = []
            # arch is fixed for now
            ret = self.osc.build.get_binary_list(prj, repo, 'x86_64', pkg)
            for file in re.findall(regex, str(etree.tostring(ret))):
                rpm = file[0]
                if Path(path_dest, rpm).exists():
                    print('\t{} already downloaded, skipping.'.format(rpm))
                    continue

                rpms.append( (prj, repo, 'x86_64', pkg, rpm, path_dest) )

            self.do_work(self.download_binary_rpms, rpms)

    def download_binary_rpms(self, args):
        prj, repo, arch, pkg, filename, dest = args
        try:
            self.osc.build.download_binary(prj, repo, arch, pkg, filename, dest)

            print('\t{}: ok'.format(filename))
        except OSError as e:
            if e.errno == errno.EEXIST:
                print('\t{}: already downloaded. skipping.'.format(filename))
            else:
                raise RuntimeError('download error on {}: {}'.format(prj, filename))

    def apply_filter(self, item_list):
        if not self.cfg.filter:
            return item_list

        filtered = []
        for item in item_list:
            if not re.match(self.cfg.filter, item.replace('_', '.')):
                continue

            filtered.append(item)

        return filtered

    def download(self):
        for result in self.get_projects().findall('project'):
            prj = result.get('name')

            if self.cfg.filter and not re.match(self.cfg.filter, prj):
                continue

            archs = result.xpath('repository/arch')
            rpms = []
            for arch in archs:
                ret = self.osc.build.get_binary_list(prj, 'devbuild', arch, 'klp')
                rpm_name = '{}.rpm'.format(arch)
                for rpm in ret.xpath('binary/@filename'):
                    if not rpm.endswith(rpm_name):
                        continue

                    if 'preempt' in rpm:
                        continue

                    # Create a directory for each arch supported
                    dest = Path(self.cfg.bsc_download, str(arch))
                    dest.mkdir(exist_ok=True)

                    rpms.append( (prj, 'devbuild', arch, 'klp', rpm, dest) )

            print('Downloading {} packages'.format(prj))
            self.do_work(self.download_binary_rpms, rpms)

    def status(self):
        prjs = {}
        for prj in self.get_project_names():
            prjs[prj] = {}

            for res in self.osc.build.get(prj).findall('result'):
                code = res.xpath('status/@code')[0]
                prjs[prj][res.get('arch')] = code

        for prj, archs in prjs.items():
            st = []
            for k, v in archs.items():
                st.append('{}: {}'.format(k, v))
            print('{}\t{}'.format(prj, '\t'.join(st)))

    def cleanup(self):
        prjs = self.get_project_names()

        if len(prjs) == 0:
            return

        print('{} projects found.'.format(len(prjs)))

        prjs = self.apply_filter(prjs)

        print('Deleting {} projects...'.format(len(prjs)))

        self.do_work(self.delete_project, prjs)
