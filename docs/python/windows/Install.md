## Windows安装Python环境注意事项

### 1、安装conda

https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe

安装路径不要选C盘，否则执行conda命令可能报权限不够。

在conda安装目录有个.condarc文件，写入以下配置修改镜像源

```.condarc
channels:
  - http://mirrors.pku.edu.cn/anaconda/pkgs/main
  - http://mirrors.pku.edu.cn/anaconda/pkgs/r
  - http://mirrors.pku.edu.cn/anaconda/pkgs/msys2

show_channel_urls: true

custom_channels:
  conda-forge: http://mirrors.pku.edu.cn/anaconda/cloud
  msys2: http://mirrors.pku.edu.cn/anaconda/cloud
  bioconda: http://mirrors.pku.edu.cn/anaconda/cloud
  menpo: http://mirrors.pku.edu.cn/anaconda/cloud
  pytorch: http://mirrors.pku.edu.cn/anaconda/cloud
  pytorch-lts: http://mirrors.pku.edu.cn/anaconda/cloud
  simpleitk: http://mirrors.pku.edu.cn/anaconda/cloud

#如果需要代理的话配置代理
#proxy_servers:
#  http: http://{user}:{password}@proxy.huawei.com:8080
#  https: http://{user}:{password}@proxy.huawei.com:8080

ssl_verify: false

allow_other_channels: true
```



### 2、修改pip镜像源

在C:\Users\UserName创建一个pip文件夹，新建pip.ini文件，写入如下配置

```.ini
[global]
trusted-host=mirrors.tools.huawei.com
index-url=https://mirrors.tools.huawei.com/pypi/simple
```

