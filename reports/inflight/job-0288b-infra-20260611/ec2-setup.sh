#!/usr/bin/env bash
# GRACE-2 agent on AWS EC2 (sprint-14-aws). Run with:  bash /tmp/grace2-ec2-setup.sh
# Creates: IAM role (SCOPED Bedrock invoke + SSM + S3 read), security group,
# and a t3.large Amazon Linux 2023 instance running the Bedrock agent.
set -euo pipefail
R=us-west-2
VPC=vpc-01b7ce297bb3a95e9

echo "== scoped Bedrock invoke policy =="
cat > /tmp/grace2-bedrock-policy.json <<'JSON'
{ "Version": "2012-10-17", "Statement": [
  { "Effect": "Allow",
    "Action": ["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],
    "Resource": "*" } ] }
JSON
cat > /tmp/ec2-trust.json <<'JSON'
{ "Version": "2012-10-17", "Statement": [
  { "Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole" } ] }
JSON

echo "== IAM role + instance profile =="
aws iam create-role --role-name grace2-agent-ec2 \
  --assume-role-policy-document file:///tmp/ec2-trust.json >/dev/null 2>&1 || echo "  (role exists)"
aws iam put-role-policy --role-name grace2-agent-ec2 \
  --policy-name bedrock-invoke --policy-document file:///tmp/grace2-bedrock-policy.json
aws iam attach-role-policy --role-name grace2-agent-ec2 \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore 2>/dev/null || true
aws iam attach-role-policy --role-name grace2-agent-ec2 \
  --policy-arn arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess 2>/dev/null || true
aws iam create-instance-profile --instance-profile-name grace2-agent-ec2 >/dev/null 2>&1 || echo "  (profile exists)"
aws iam add-role-to-instance-profile --instance-profile-name grace2-agent-ec2 \
  --role-name grace2-agent-ec2 2>/dev/null || true

echo "== security group (WS 8765 + catalog 8766, public for the demo) =="
SG=$(aws ec2 create-security-group --region $R --group-name grace2-agent-sg \
  --description "GRACE-2 agent WS + catalog" --vpc-id $VPC --query GroupId --output text 2>/dev/null \
  || aws ec2 describe-security-groups --region $R --filters Name=group-name,Values=grace2-agent-sg \
       --query "SecurityGroups[0].GroupId" --output text)
aws ec2 authorize-security-group-ingress --region $R --group-id "$SG" \
  --ip-permissions IpProtocol=tcp,FromPort=8765,ToPort=8766,IpRanges='[{CidrIp=0.0.0.0/0,Description=grace2-demo}]' \
  2>/dev/null || echo "  (ingress exists)"

echo "== resolve AMI + a default public subnet =="
AMI=$(aws ssm get-parameter --region $R \
  --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query Parameter.Value --output text)
SUBNET=$(aws ec2 describe-subnets --region $R --filters Name=default-for-az,Values=true \
  --query "Subnets[0].SubnetId" --output text)

echo "== launch t3.large (waits ~20s for instance profile propagation) =="
sleep 20
IID=$(aws ec2 run-instances --region $R --image-id "$AMI" --instance-type t3.large \
  --iam-instance-profile Name=grace2-agent-ec2 \
  --security-group-ids "$SG" --subnet-id "$SUBNET" --associate-public-ip-address \
  --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=30,VolumeType=gp3}' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=grace2-agent}]' \
  --query "Instances[0].InstanceId" --output text)
echo "INSTANCE=$IID  SG=$SG  SUBNET=$SUBNET" | tee /tmp/grace2-aws-ids.txt
aws ec2 wait instance-running --region $R --instance-ids "$IID"
PUBDNS=$(aws ec2 describe-instances --region $R --instance-ids "$IID" \
  --query "Reservations[0].Instances[0].PublicDnsName" --output text)
echo "PUBLIC_DNS=$PUBDNS" | tee -a /tmp/grace2-aws-ids.txt
echo ""
echo "DONE. Instance $IID is up at $PUBDNS"
echo "Tell Claude the instance is up; it will install + start the Bedrock agent via SSM."
